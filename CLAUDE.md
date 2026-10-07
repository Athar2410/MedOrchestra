# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What MedOrchestra is

A multi-agent clinical decision support system (CDSS) implementing Borkowski et al. (2025), "Multiagent AI Systems in Health Care" (PMC12360800), as a B.Tech capstone. Requirements live in `MedOrchestra_PRD(1).pdf`. Synthetic/open data only — no EHR/FHIR, no clinical deployment. Scope is intentionally capped at **4 agents** (Triage, Diagnostician, Drug Safety, Critique; `report` is an assembly node, not an agent). The Critique agent and its feedback loop are the paper's novel contribution.

Work proceeds in phases; the roadmap and per-phase status are in `README.md`. Phases 1–5 are done: all four agents are real, and runs are persisted with history/replay and PDF export. **Phase 6 (evaluation) is in progress — see "Resume here" below.**

**Evaluation** (`backend/eval/`): `ddxplus.py` builds 50 cases (one per DDXPlus class + 1) into `data/ddxplus/cases.jsonl`. `run_eval.py` runs pipeline + single-LLM baseline + LLM judge per case. It is resumable, paces 20 s between cases, retries degraded cases, and `--rejudge` re-scores saved predictions. `ddi_recall.py` handles drug-interaction recall (no Groq). Results are in `eval/results/`.
- The ablation needs no extra runs: the "no Critique" arm is the Diagnostician's first-pass output, taken from the same run's events.
- The judge runs on Qwen (a different family from the gpt-oss models it grades) and is given the 49 DDXPlus class names. Without that context it credited "viral URI" for Influenza and failed "allergic rhinitis" for Allergic sinusitis. It still made one lenient error (supraglottitis accepted for laryngitis).
- **Groq free tier: 8K tokens/min and 200K tokens/day per model.** One evaluated case costs ~15–20K tokens, so ~10–13 cases/day fit on `gpt-oss-120b`.

## Resume here (Phase 6, as of 2026-10-07)

The user chose the free route: finish the 50 DDXPlus cases across days within Groq's free quota. **Start a fresh session (not `--continue`); everything needed is in this file.** 44 of 50 cases are done. Remaining: `ddx-045`–`ddx-049` plus `ddx-044` (saved as degraded, re-runs automatically). That fits in one day.

**Run in WSL, not Windows.** Windows Smart App Control switched to enforce mode on 2026-10-07 and blocks torch's unsigned `c10.dll`, so MedCPT and retrieval can't load in `backend/.venv`. WSL (Ubuntu 24.04) has a venv at `~/mo-venv`, created with `uv` because Ubuntu has no `python3-venv` and no sudo was used. It pins Python 3.14, torch 2.14.1+cpu and transformers 5.18.0 to match the Windows venv, and runs on the repo in place (no editable install; `-m` from `backend/` finds `app`). All 81 tests pass there.

1. Check the quota: each of `gpt-oss-120b` and Qwen allows 200K tokens/day (rolling 24 h) and 8K/min. On 2026-10-07, 20 cases (including retries) used up both. **A tiny probe request succeeds even when the daily quota is exhausted.** Groq only rejects requests larger than the remaining daily quota, and `llm.py` logs just `HTTP 429` without the reason. To see the real limit, send a ~4K-token request with `max_tokens: 1` and read `"message"` (it says `tokens per day (TPD): … Used …`).
2. Run in the background: `wsl -- bash -lc "cd /mnt/c/MedOrchestra/backend && ~/mo-venv/bin/python -u -m eval.run_eval > <log> 2>&1"`. It resumes and re-runs degraded cases. Progress lines look like `[n/N] ddx-0NN …`. Repeated `degraded … retrying` or `attempt N error: … HTTP 429` usually means the daily quota is gone: probe as in step 1, then stop it. Stopping the `wsl` wrapper may leave the Python process running inside Linux, so check with `wsl -- ps -eo pid,args`.
3. After all 50: run `python -m eval.run_eval --summary`, then spot-check misses by hand. **Known judge error to correct in the write-up:** `ddx-002` Acute laryngitis — the final arm's "Acute supraglottitis (acute laryngitis)" was credited, but supraglottitis = epiglottitis, a separate DDXPlus class. Also re-run `python -m eval.ddi_recall --n 150` only if `drug_names.py` changed.
4. Commit the results, update README Phase 6 → ✅, and summarise the final numbers for the paper.

Interim results (25 cases, Qwen judge): with Critique top-1 68% / top-3 88% (64/84 after the manual correction); no Critique 60/72; single-LLM 64/84. On re-routed cases top-3 went 50% → 79%. Retrieval precision 71%. DDI recall 100% generic / 95% brand. Latency median 14.6 s (52% ≤15 s; single-pass 10.7 s). Calibration proxy r = 0.36. The likely final misses are latency (re-routes) and calibration (the PRD wants physician ratings; ours is a proxy).

Interim results at 44 cases (`eval/results/summary.md`): with Critique top-1 59% / top-3 73% (70% after the ddx-002 correction); no Critique 57/66; single-LLM 55/75. Re-routed cases top-3 50% → 62%. Retrieval precision 72%. Latency median 15.1 s (49% ≤15 s). Calibration proxy r = 0.47. Cases 26–44 ran in WSL. End-to-end latency is the same in both environments (median 14.6 s Windows vs 15.4 s WSL, clean first attempts), because 429 retries dominate, so they are reported together. The user presented on 2026-10-08 with these 44 cases, and README "Evaluation results" holds the presented numbers. The re-route wording fix was deliberately not made before the presentation. Findings from day 2 to write up:
- **The Critique hurt two cases:** `ddx-037` pulmonary neoplasm and `ddx-038` SLE. In both, the first pass had the right answer at #1, the Critique gave 0.1 confidence, and the re-routed pass dropped it. The likely cause is the re-route prompt "A senior reviewer **rejected** the previous differential" (`diagnostician.py:86`), which pushes the model to replace everything. The obvious fix is "revise" wording that keeps strong candidates. It was not changed mid-eval, so all 50 cases measure one system.
- **The Critique helped:** `ddx-040` scombroid (only the final arm got it).
- **Re-routes that changed nothing:** `ddx-029` myocarditis and `ddx-034` pneumonia (the second pass repeated the first; the baseline had pneumonia).
- **Bias toward dangerous diagnoses:** the models put ACS/PE/dissection ahead of the benign correct label (`ddx-043` stable angina, `ddx-029` myocarditis). `ddx-027` "Localized edema" is a symptom-level label.
- **Eval blind spot:** `degraded` doesn't flag a failed hypothesis step on a re-route, or a call served by `llm_fallback_model`. Note it as a limitation.

## Commands

Windows dev machine. Python uses the venv at `backend/.venv`; Node was installed via winget, so a fresh PowerShell may need its `Path` refreshed.

```powershell
# backend/
.\.venv\Scripts\python -m pip install -e ".[dev,ml]" --extra-index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python -m pytest -q                                  # all tests
.\.venv\Scripts\python -m pytest tests/test_graph.py::test_low_confidence_reroutes_exactly_once
.\.venv\Scripts\ruff check . ; .\.venv\Scripts\ruff format .
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
.\.venv\Scripts\python -m pipelines.download_ddinter    # into backend/data/ddinter (gitignored, CC BY-NC-SA)
.\.venv\Scripts\python -m pipelines.migrate             # apply backend/migrations/*.sql (idempotent)
.\.venv\Scripts\python -m pipelines.ingest_pubmed --limit 50000  # done: 50K in Supabase; resumable, state in backend/data/pubmed

# frontend/
npm run dev      # http://localhost:3000, expects API at NEXT_PUBLIC_API_URL (default http://127.0.0.1:8000 — `localhost` costs ~2 s per request on this machine via IPv6 fallback)
npm run lint
npm run build
```

`frontend/` is **Next.js 16** (React 19, Tailwind v4) — its `AGENTS.md` says APIs differ from older Next.js; read `frontend/node_modules/next/dist/docs/` before using unfamiliar APIs.

## Architecture

**Graph** (`backend/app/graph/builder.py`): `START → triage → {diagnostician ∥ drug_safety} → critique → (diagnostician | report) → END`.
- Diagnostician and Drug Safety run in the same LangGraph superstep; critique runs once after both. Any `ClinicalState` key written by both parallel nodes needs a reducer (`graph/state.py`); today only `agent_logs` is shared.
- Re-route: critique sets `critique.reroute_requested` and increments `reroute_count` when confidence < `critique_threshold` and `reroute_count < max_reroutes` (config). Only the Diagnostician re-runs; Drug Safety does not.

**Agents** (`backend/app/agents/`): each is `async def agent(state, ctx) -> dict` (partial state update) wrapped in `@agent_node("name")` from `agents/base.py`. The wrapper emits `agent_started` / `agent_completed` (with `duration_ms` and JSON `output`) / `agent_failed` and appends to `agent_logs`. Inside an agent, use `ctx.think(msg)` for live progress and `ctx.emit(type, **data)` for other events (the critique emits `reroute`). `attempt` is `reroute_count + 1` for diagnostician/critique, so retries show up as attempt 2.

**Streaming** (`backend/app/runs.py`, `backend/app/api.py`): `POST /api/cases` starts the graph as a background task and returns `run_id`. Events from LangGraph's `custom` stream are buffered on the `Run`. `GET /api/runs/{id}/stream` is SSE: `id:` is the event index and `data:` is JSON with a `type` field. Subscribers replay from `Last-Event-ID`, so browser `EventSource` reconnects never re-run a case. The stream always ends with a `done` event, appended atomically with `run.done = True`. Finished runs are saved to the Supabase `runs` table (`services/run_store.py`, `migrations/003`) with the case, report and full event list (~112 kB per run). `RunManager.load()` falls back to the database, so `GET /api/runs/{id}` and the SSE stream replay past runs after a restart. `GET /api/runs` lists recent runs for the UI's "Recent cases" panel. Saving is best-effort (a DB failure never affects the live result).

**Event contract** is mirrored by hand in `frontend/src/lib/types.ts` (`RunEvent`, report schemas) — keep it in sync with `backend/app/schemas.py` and `agents/base.py`. `frontend/src/hooks/useRunStream.ts` folds events into per-agent, per-attempt UI state.

**LLM** (`backend/app/services/llm.py`): agents call `get_llm().structured(system=, user=, schema=PydanticModel, budget_seconds=)`. `get_llm()` returns `None` without `GROQ_API_KEY`, and **every agent must work in that case and when `LLMError` is raised** (rules fallback). Requests use Groq strict `json_schema` mode. `strict_schema()` converts Pydantic schemas: refs inlined, all fields required, constraint keywords stripped (Pydantic still validates them, with one repair round-trip). Each call has a wall-clock budget. The primary model gets 60% of it, then `llm_fallback_model` gets the rest. Responses are cached in SQLite keyed by the request body. Agent budgets are module constants (triage 6s, drug explanations 8s) sized for the PRD's ≤15s end-to-end target. Model IDs are config: Llama 3.3 70B is not available on this Groq account.
- Groq quirks found live: Qwen + strict mode + `reasoning_format: "hidden"` produces garbage, so it uses `"parsed"`. Qwen strict mode also fails randomly with HTTP 400 `json_validate_failed`, which is treated as retryable. Free-tier 503s and 429s are frequent. httpx timeouts are per read, so the total is enforced with `asyncio.timeout`.

**Safety rules in agents:** Triage urgency = max(NEWS2, LLM). The LLM may escalate but never downgrade. Without an LLM, the red-flag phrase rules (`agents/red_flags.py`) escalate to HIGH, so classic ACS with normal vitals is never LOW. Drug interaction severity always comes from DDInter. The LLM only writes `explanation`/`clinical_action` (`explanation_source="llm"`) and only for graded pairs, never for `unknown` ones.

**Diagnostician** (`agents/diagnostician.py`) runs four steps:
1. The LLM proposes up to 5 hypotheses, each with a PubMed query. On a re-route, the critique's flags and questions are added to both prompts.
2. The case query plus every hypothesis query go to `services/retrieval.py`. That encodes them with the MedCPT query encoder, calls the `hybrid_search` SQL function per query (pgvector `<#>` inner product + full-text with OR'd terms, RRF-fused), and pools candidates round-robin capped at `rerank_pool`. The cross-encoder scores each candidate against the query that found it, and `evidence_k` abstracts are kept round-robin across queries.
3. The LLM picks the top 3 citing `[E#]` ids only. Ids not in the retrieved list are dropped in code.
4. ICD-11 codes are looked up once, on the final differential, in the report node (`agents/report.py` → `services/icd.py`). WHO's `autocode` returns 500 on release 2026-01, so it uses `search` + a qualifier rule ("Dengue fever" must not become "Severe dengue"), with `autocode` on 2025-01 as the fallback.

Without an LLM there is no differential (empty list). Without retrieval, the diagnoses are uncited.
- Corpus: 50K abstracts, 130 topics, ~362 MB of the 500 MB free tier. At this size the keyword side of `hybrid_search` must stay bounded (`migrations/002`): it ANDs the terms first, falls back to OR, and ranks at most 300 matches. Ranking all OR matches (~15K rows) took ~7 s on a cold cache and silently blew the retrieval budget. Hybrid queries run in parallel on a 6-connection pool with `statement_timeout=3s`. The ingestion reconnects when the Supabase pooler drops the connection.
- Database code is **sync psycopg in `asyncio.to_thread`**, because psycopg async does not work on the Windows Proactor loop. MedCPT inference shares that thread and holds a lock. `pubmed_chunks` stores `halfvec(768)` to fit the Supabase free tier. RLS is on (the backend connects as the owner).
- CPU costs on the dev laptop (i5-1235U): article embedding ~5–8/s with length-sorted batches (unsorted padding made it 2.4/s), and the cross-encoder ~0.15 s/doc. Int8 quantization was rejected (0.94 cosine to fp32).

**Drug data** (`services/drug_graph.py`, `services/drug_names.py`): an undirected graph of 1,939 drugs and 160K pairs. Duplicate pairs across DDInter's per-ATC files keep the most severe graded level. DDInter has no mechanism text and mixes INN/USAN names (`acetylsalicylic acid` but `salbutamol`), so names resolve via exact → synonym groups → RxNorm (`approximateTerm` score ≥ 7, then ingredients, which handles brands and combination products) → fuzzy.

**Tests** (`tests/conftest.py`) set env vars before importing app modules, which override `backend/.env`: no Groq key, LLM cache, RxNorm, `DATABASE_URL` or ICD credentials, and `DDINTER_DIR=tests/fixtures/ddinter`. Tests never touch the network. Use the `fake_llm({SchemaName: response | callable | exception})` and `fake_retriever(evidence | exception)` fixtures; `make_evidence()` builds `Evidence` rows. Agents can be called directly (outside a graph run, stream events are dropped).

**Critique** (`agents/critique.py`, Phase 4) runs two checks concurrently:
- An LLM "senior attending" review on `critique_model` (Qwen, a different family; falls back to `llm_fallback_model`). It returns confidence, a one-sentence concern per diagnosis, missed diagnoses, ≤3 clarification questions and likely treatments.
- A code-side citation check: `retriever.score()` runs the MedCPT cross-encoder on (condition, cited abstract). `SUPPORT_THRESHOLD = 8` is provisional (on-topic 12–16, irrelevant 2–7).

An unsupported leading diagnosis caps confidence at 0.55, which forces a re-route. Likely treatments are checked against current meds in DDInter (`treatment_cautions`; unknown-severity pairs are dropped). Flags and missed diagnoses reach the re-routed Diagnostician through `_feedback()`. Without an LLM, the deterministic `_rules_review()` is used.

## Open items
- **Latency:** single-pass cases take ~8–12 s ✅. **Re-routed cases take ~18.6 s** (down from ~23 s): ICD-11 coding now runs once in the report node, and a re-route reuses the first pass's evidence and only searches new queries. The remaining cost is the second search + rerank (~4–5 s) for the new hypotheses. Further levers trade quality (smaller `rerank_pool`) or money (a paid Groq tier, to avoid 429 retries).
- **`min_rerank_score = -5` and `SUPPORT_THRESHOLD = 8` are provisional.** Tune both in Phase 6 against labelled relevance (PRD RAG precision ≥0.70).
- **DDInter has gaps:** e.g. no ACE inhibitor + spironolactone pair. That limits DDI recall; document it for the paper and don't invent pairs.
- The UI was verified in Brave via Claude-in-Chrome (Phases 3–5), and the user confirmed Export PDF works. Don't click Export PDF with the browser tool: the print dialog blocks it. In Brave the extension's ref-based clicks sometimes don't register; JS `element.click()` via `javascript_tool` works.
- **Supabase space:** the corpus uses ~362 MB of 500 MB, and runs take ~112 kB each (about 1,200 runs of headroom). Prune old runs or trim stored `output` payloads if evaluation needs more.
- **Skipped on purpose:** Langfuse tracing (the stored event stream already captures every agent step) and server-side PDF generation (browser print-to-PDF via `exportPdf()`, light mode, print CSS).

## Decided stack deviations from the PRD

FastAPI (not Flask), Supabase pgvector (not local Postgres), DDInter + RxNorm (not DrugBank CSV, which has no interaction data), NEWS2 (not SEWS), Drug Safety parallel with Diagnostician, MedCPT/bge-v1.5 + Postgres full-text hybrid retrieval + reranker, a Critique LLM from a different model family than the Diagnostician. Pitfalls to remember:
- Groq `llama3-70b-8192` is retired — keep model names in config.
- MIMIC-III labels are ICD-9 but the system outputs ICD-11 — eval needs mapping or name-level matching; DDXPlus is an additional eval set.
- Supabase free tier (500 MB) fits ~50K embedded abstracts, not 500K. Store one abstract per chunk.
- Drug interactions are symmetric — use an undirected graph.

## Evaluation targets (PRD §9)

Top-1 ≥65%, Top-3 ≥80%, DDI recall ≥80%, RAG precision ≥0.70, latency ≤15s (50 cases), critique calibration Pearson r ≥0.6. Plus an ablation without the Critique agent and baselines (single-LLM, rule-based). Cache LLM responses so eval runs are reproducible.
