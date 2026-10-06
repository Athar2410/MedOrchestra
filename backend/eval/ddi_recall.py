"""Drug-interaction recall: can Drug Safety find known DDInter pairs from user-style input?

Samples graded DDInter pairs, then runs the real Drug Safety agent (LLM off) on each pair
entered two ways: generic names, and US brand names from RxNorm (exercising name
resolution: brand -> RxNorm ingredient -> synonym groups -> DDInter name). A hit = the
pair is reported with DDInter's severity. This measures the pipeline, not DDInter's own
coverage (its gaps, e.g. ACE inhibitor + spironolactone, are outside this number).

    .\\.venv\\Scripts\\python -m eval.ddi_recall --n 150
"""

import argparse
import asyncio
import json
import random

import httpx

from app.agents.drug_safety import drug_safety_agent
from app.config import get_settings
from app.schemas import CaseInput
from app.services.drug_graph import get_drug_graph
from app.services.drug_names import SYNONYM_GROUPS
from app.services.llm import use_llm
from eval.run_eval import RESULTS

BRANDS_FILE = RESULTS / "rxnorm_brands.json"


async def brand_name(http: httpx.AsyncClient, drug: str) -> str | None:
    """First RxNorm brand name for an ingredient (trying INN/USAN synonyms)."""
    names = [drug, *sorted(next((g for g in SYNONYM_GROUPS if drug in g), set()) - {drug})]
    for name in names:
        ids = (await http.get("/rxcui.json", params={"name": name})).json()
        rxcui = (ids.get("idGroup", {}).get("rxnormId") or [None])[0]
        if not rxcui:
            continue
        related = (await http.get(f"/rxcui/{rxcui}/related.json", params={"tty": "BN"})).json()
        groups = related.get("relatedGroup", {}).get("conceptGroup") or []
        brands = sorted(p["name"] for g in groups for p in g.get("conceptProperties") or [])
        if brands:
            return brands[0]
    return None


async def found(a: str, b: str, drug_a: str, drug_b: str, severity: str) -> bool:
    out = await drug_safety_agent({"case": CaseInput(chief_complaint="eval", medications=[a, b])})
    return any(
        {i.drug_a, i.drug_b} == {drug_a, drug_b} and i.severity == severity
        for i in out["drug_interactions"]
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=150)
    args = parser.parse_args()
    graph = get_drug_graph().graph
    graded = sorted(
        (a, b, d["severity"]) for a, b, d in graph.edges(data=True) if d["severity"] != "unknown"
    )
    pairs = random.Random(11).sample(graded, args.n)

    RESULTS.mkdir(exist_ok=True)
    brands = json.loads(BRANDS_FILE.read_text()) if BRANDS_FILE.exists() else {}
    async with httpx.AsyncClient(base_url=get_settings().rxnorm_base_url, timeout=15) as http:
        for drug in sorted({d for a, b, _ in pairs for d in (a, b)} - brands.keys()):
            brands[drug] = await brand_name(http, drug)
    BRANDS_FILE.write_text(json.dumps(brands, indent=1, sort_keys=True))

    generic_hits, brand_hits, misses = [], [], []
    with use_llm(None):  # recall does not depend on explanations; no Groq calls
        for a, b, sev in pairs:
            generic_hits.append(await found(a.title(), b.title(), a, b, sev))
            if brands.get(a) and brands.get(b):
                hit = await found(brands[a], brands[b], a, b, sev)
                brand_hits.append(hit)
                if not hit:
                    misses.append(f"{brands[a]} ({a}) + {brands[b]} ({b}) [{sev}]")

    def pct(xs: list[bool]) -> str:
        return f"{100 * sum(xs) / len(xs):.0f}% ({sum(xs)}/{len(xs)})" if xs else "n/a"

    summary = "\n".join(
        [
            f"# Drug-interaction recall ({args.n} graded DDInter pairs, seed 11)",
            "",
            f"- Generic names: {pct(generic_hits)}",
            f"- US brand names via RxNorm: {pct(brand_hits)} "
            f"({len(brand_hits)} pairs where both drugs have a US brand)",
            "- PRD target: ≥80%",
            "",
            "Brand-name misses:",
            *[f"- {m}" for m in misses[:20]],
        ]
    )
    (RESULTS / "ddi_recall.md").write_text(summary, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
