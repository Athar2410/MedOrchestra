"""Phase 6 evaluation on DDXPlus cases (built by `python -m eval.ddxplus`).

Per case (resumable — one JSON line per finished case in eval/results/cases.jsonl):
  - full pipeline run: final top-3, first-pass top-3 (the Diagnostician before any
    Critique re-route = the "without Critique" ablation arm), latency, critique
    confidence, retrieved abstracts
  - single-LLM baseline: the same model, one prompt, no agents/retrieval
  - LLM judge: which predicted names match the true pathology (synonyms and more
    specific subtypes count) and which retrieved abstracts are relevant to their query

    .\\.venv\\Scripts\\python -m eval.run_eval            # run / resume, then summarise
    .\\.venv\\Scripts\\python -m eval.run_eval --summary  # summarise only

    .\\.venv\\Scripts\\python -m eval.run_eval --rejudge  # re-score saved predictions

The LLM response cache makes re-runs reproducible. The judge runs on `critique_model`
(Qwen), a different family from the gpt-oss Diagnostician and baseline it grades, so it
has no self-preference; spot-check its verdicts by hand for the paper.
"""

import argparse
import asyncio
import json
import logging
import statistics
import time
from pathlib import Path

import psycopg
from pydantic import BaseModel

from app.config import get_settings
from app.graph.builder import build_graph
from app.schemas import CaseInput
from app.services.drug_graph import get_drug_graph
from app.services.llm import get_llm
from app.services.retrieval import get_retriever
from eval.ddxplus import CASES_FILE, ddxplus_classes

RESULTS = Path(__file__).parent / "results"
RESULTS_FILE = RESULTS / "cases.jsonl"
SUMMARY_FILE = RESULTS / "summary.md"
TARGET_LATENCY_S = 15.0
# A different model family from the gpt-oss models being judged.
JUDGE_MODEL = get_settings().critique_model

BASELINE_PROMPT = """You are an internal medicine specialist. Give the 3 most likely
diagnoses for this patient, most likely first, as standard disease names."""

MATCH_PROMPT = """You are grading a diagnostic system on DDXPlus, whose patients each have
one true diagnosis from this fixed list of classes:
{classes}

For each candidate diagnosis decide which class it corresponds to, and answer `matches`
true only if that class is the TRUE diagnosis. Synonyms, abbreviations and more specific
subtypes map to their class (e.g. "NSTEMI" -> "Possible NSTEMI / STEMI"; "allergic
rhinitis" -> "Allergic sinusitis", since the list has no separate rhinitis class).
A candidate that maps to a DIFFERENT class in the list does not match (e.g. "viral upper
respiratory infection" -> "URTI", not "Influenza"), and neither does a condition that is
merely related. Return one item per candidate, copying its name exactly."""

RELEVANCE_PROMPT = """You are grading a literature search done to support a differential
diagnosis. Each query names a condition plus some of the patient's symptoms as context.
An abstract is relevant if it is substantially about the queried condition (its
presentation, diagnosis, differential or management); it does not need to mention the
symptom words. It is not relevant if it is about a different condition or only mentions
the queried one in passing. Return one item per abstract."""


class Baseline(BaseModel):
    diagnoses: list[str]


class NameMatch(BaseModel):
    name: str
    matches: bool


class Judgement(BaseModel):
    items: list[NameMatch]


class AbstractRelevance(BaseModel):
    index: int
    relevant: bool


class RelevanceJudgement(BaseModel):
    items: list[AbstractRelevance]


async def run_pipeline(case: CaseInput) -> dict:
    first_pass: list[str] | None = None
    evidence: list[dict] = []
    report = None
    started = time.perf_counter()
    async for mode, chunk in build_graph().astream(
        {"case": case, "reroute_count": 0}, stream_mode=["custom", "updates"]
    ):
        if mode == "custom" and chunk.get("type") == "agent_completed":
            if chunk["agent"] == "diagnostician":
                names = [d["condition"] for d in chunk["output"].get("diagnoses", [])]
                first_pass = names if first_pass is None else first_pass
                evidence = chunk["output"].get("evidence", [])
        elif mode == "updates" and "report" in chunk:
            report = chunk["report"]["report"]
    return {
        "latency_s": round(time.perf_counter() - started, 2),
        # An LLM step fell back (rate limit / outage): the result measures Groq, not the
        # system, so the case is retried and, if still degraded, reported separately.
        "degraded": not first_pass
        or not report.diagnoses
        or report.critique.method != "llm"
        or report.triage.method != "llm",
        "final": [d.condition for d in report.diagnoses],
        "first_pass": first_pass or [],
        "reroutes": report.reroutes,
        "critique_confidence": report.critique.confidence_score,
        "critique_method": report.critique.method,
        "triage_urgency": report.urgency,
        "evidence": [
            {
                "pmid": e["pmid"],
                "title": e["title"],
                "query": e["query"],
                "abstract": e["abstract"][:700],
            }  # fmt: skip
            for e in evidence
        ],
    }


async def judge_matches(truth: str, names: list[str]) -> dict[str, bool]:
    unique = list(dict.fromkeys(n for n in names if n))
    if not unique:
        return {}
    listing = "\n".join(f"- {n}" for n in unique)
    result = await get_llm().structured(
        system=MATCH_PROMPT.format(classes=", ".join(ddxplus_classes())),
        user=f"True diagnosis: {truth}\n\nCandidates:\n{listing}",
        schema=Judgement,
        model=JUDGE_MODEL,
        budget_seconds=30,
    )
    verdict = {i.name: i.matches for i in result.items}
    return {n: verdict.get(n, False) for n in unique}


async def judge_relevance(evidence: list[dict]) -> list[bool]:
    if not evidence:
        return []
    listing = "\n\n".join(
        f"[{i}] Query: {e['query']}\nTitle: {e['title']}\nAbstract: {e['abstract']}"
        for i, e in enumerate(evidence, start=1)
    )
    result = await get_llm().structured(
        system=RELEVANCE_PROMPT,
        user=listing,
        schema=RelevanceJudgement,
        model=JUDGE_MODEL,
        budget_seconds=30,
    )
    verdict = {i.index: i.relevant for i in result.items}
    return [verdict.get(i, False) for i in range(1, len(evidence) + 1)]


async def score(truth: str, run: dict) -> None:
    """Judge every arm's predictions against the truth; sets <arm>_top1/_top3 in place."""
    matches = await judge_matches(truth, run["final"] + run["first_pass"] + run["baseline"])
    for arm in ("final", "first_pass", "baseline"):
        hits = [matches.get(n, False) for n in run[arm]]
        run[f"{arm}_top1"] = bool(hits[:1] and hits[0])
        run[f"{arm}_top3"] = any(hits[:3])


def fetch_abstracts(pmids: set[int]) -> dict[int, str]:
    """Abstracts aren't kept in the results file; reload them from Supabase to re-judge."""
    with psycopg.connect(get_settings().database_url) as conn:
        rows = conn.execute(
            "select pmid, abstract from pubmed_chunks where pmid = any(%s)", (list(pmids),)
        ).fetchall()
    return dict(rows)


def save(done: dict[str, dict]) -> None:
    RESULTS_FILE.write_text("".join(json.dumps(r) + "\n" for r in done.values()), encoding="utf-8")


async def evaluate_case(row: dict) -> dict:
    case = CaseInput.model_validate(row["case"])
    run = await run_pipeline(case)
    baseline = await get_llm().structured(
        system=BASELINE_PROMPT,
        user=case.chief_complaint + f"\nPatient: {case.age}y {case.sex or ''}",
        schema=Baseline,
        budget_seconds=30,
    )
    run["baseline"] = baseline.diagnoses[:3]
    await score(row["truth"], run)
    run["evidence_relevant"] = await judge_relevance(run["evidence"])
    for e in run["evidence"]:
        e.pop("abstract")  # keep the results file small
    return {"id": row["id"], "truth": row["truth"], **run}


def _pct(xs: list[bool]) -> str:
    return f"{100 * sum(xs) / len(xs):.0f}% ({sum(xs)}/{len(xs)})" if xs else "n/a"


def summarise(all_rows: list[dict]) -> str:
    rows = [r for r in all_rows if not r["degraded"]]
    degraded = len(all_rows) - len(rows)
    # Retries reuse cached LLM responses, so only clean first attempts are timed.
    lat = sorted(r["latency_s"] for r in rows if r["attempts"] == 1) or [float("nan")]
    single = [r["latency_s"] for r in rows if r["reroutes"] == 0 and r["attempts"] == 1]
    rerouted = [r for r in rows if r["reroutes"] > 0]
    relevant = [x for r in rows for x in r["evidence_relevant"]]
    conf = [r["critique_confidence"] for r in rows]
    correct = [float(r["final_top1"]) for r in rows]
    try:
        calibration = f"{statistics.correlation(conf, correct):.2f}"
    except statistics.StatisticsError:
        calibration = "n/a (constant input)"
    lines = [
        f"# MedOrchestra evaluation — DDXPlus ({len(rows)} cases)",
        "",
        f"{degraded} case(s) excluded because an LLM step still failed after retries "
        "(Groq free-tier rate limits).",
        "",
        "| Arm | Top-1 | Top-3 |",
        "|---|---|---|",
        f"| MedOrchestra (final, with Critique) | {_pct([r['final_top1'] for r in rows])} "
        f"| {_pct([r['final_top3'] for r in rows])} |",
        f"| Ablation: first pass, no Critique | {_pct([r['first_pass_top1'] for r in rows])} "
        f"| {_pct([r['first_pass_top3'] for r in rows])} |",
        f"| Baseline: single LLM call | {_pct([r['baseline_top1'] for r in rows])} "
        f"| {_pct([r['baseline_top3'] for r in rows])} |",
        "",
        "PRD targets: Top-1 ≥65%, Top-3 ≥80%.",
        "",
        f"- **Re-routed cases:** {len(rerouted)}/{len(rows)}; on those, top-3 went from "
        f"{_pct([r['first_pass_top3'] for r in rerouted])} (first pass) to "
        f"{_pct([r['final_top3'] for r in rerouted])} (final)",
        f"- **Latency:** median {statistics.median(lat):.1f} s, "
        f"p90 {lat[int(0.9 * (len(lat) - 1))]:.1f} s, "
        f"≤{TARGET_LATENCY_S:.0f} s: {_pct([x <= TARGET_LATENCY_S for x in lat])}; "
        f"single-pass median {statistics.median(single) if single else float('nan'):.1f} s",
        f"- **Retrieval precision (LLM-judged):** {_pct(relevant)} of retrieved abstracts "
        "relevant to their query (target ≥70%)",
        f"- **Critique calibration (proxy):** Pearson r = {calibration} between critique "
        "confidence and final top-1 correctness (target r ≥0.6 vs physician ratings)",
        f"- **Critique ran as LLM:** {_pct([r['critique_method'] == 'llm' for r in rows])}",
        "",
        f"Judging: {JUDGE_MODEL} (different family from the judged gpt-oss models) — "
        "spot-check `cases.jsonl` by hand.",
    ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", help="only summarise saved results")
    parser.add_argument("--limit", type=int, help="evaluate at most this many cases")
    parser.add_argument("--pause", type=float, default=20, help="seconds between cases")
    parser.add_argument("--rejudge", action="store_true", help="re-score saved predictions")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    RESULTS.mkdir(exist_ok=True)

    done = {}
    if RESULTS_FILE.exists():
        done = {r["id"]: r for r in map(json.loads, RESULTS_FILE.open(encoding="utf-8"))}
    if args.rejudge:
        abstracts = fetch_abstracts({e["pmid"] for r in done.values() for e in r["evidence"]})
        for r in done.values():
            await score(r["truth"], r)
            evidence = [
                {**e, "abstract": abstracts.get(e["pmid"], "")[:700]} for e in r["evidence"]
            ]
            r["evidence_relevant"] = await judge_relevance(evidence)
            await asyncio.sleep(10)  # stay under the free tier's 8K tokens/minute
        save(done)
    elif not args.summary:
        if get_llm() is None:
            raise SystemExit("GROQ_API_KEY is required for evaluation")
        get_drug_graph()
        if retriever := get_retriever():
            retriever.warm_up()
        rows = [json.loads(line) for line in CASES_FILE.open(encoding="utf-8")]
        # Degraded results (an LLM step fell back) are re-run on resume.
        todo = [r for r in rows if r["id"] not in done or done[r["id"]]["degraded"]]
        todo = todo[: args.limit]
        for i, row in enumerate(todo, 1):
            result = None
            for attempt in range(1, 4):
                if i > 1 or attempt > 1:
                    # Free-tier token-per-minute limits: give the window time to clear.
                    await asyncio.sleep(args.pause if attempt == 1 else 60)
                try:
                    result = {**await evaluate_case(row), "attempts": attempt}
                except Exception as exc:  # one bad case must not stop a 50-case run
                    print(f"[{i}/{len(todo)}] {row['id']} attempt {attempt} error: {exc}")
                    continue
                if not result["degraded"]:
                    break
                print(f"[{i}/{len(todo)}] {row['id']} degraded (LLM fallback), retrying")
            if result is None:
                continue
            done[row["id"]] = result
            save(done)
            print(
                f"[{i}/{len(todo)}] {row['id']} {row['truth'][:30]:30} "
                f"final top3={result['final_top3']!s:5} first={result['first_pass_top3']!s:5} "
                f"base={result['baseline_top3']!s:5} {result['latency_s']:5.1f}s"
                + (" DEGRADED" if result["degraded"] else ""),
                flush=True,
            )
    summary = summarise(list(done.values()))
    SUMMARY_FILE.write_text(summary, encoding="utf-8")
    print("\n" + summary)


if __name__ == "__main__":
    asyncio.run(main())
