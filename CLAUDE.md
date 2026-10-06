# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What MedOrchestra is

A multi-agent clinical decision support system (CDSS) implementing Borkowski et al. (2025), "Multiagent AI Systems in Health Care" (PMC12360800), as a B.Tech capstone. Requirements live in `MedOrchestra_PRD(1).pdf`. Synthetic/open data only — no EHR/FHIR, no clinical deployment. Scope is intentionally capped at **4 agents** (Triage, Diagnostician, Drug Safety, Critique; `report` is an assembly node, not an agent). The Critique agent and its feedback loop are the paper's novel contribution.

Work proceeds in phases; the roadmap and per-phase status are in `README.md`. Phases 1–3 are done: Triage (NEWS2 + LLM), Drug Safety (DDInter + RxNorm + LLM) and the Diagnostician (PubMed retrieval + LLM + ICD-11) are real. The Critique is deterministic checks only until Phase 4.

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
.\.venv\Scripts\python -m pipelines.ingest_pubmed --limit 5000   # resumable; state in backend/data/pubmed

# frontend/
npm run dev      # http://localhost:3000, expects API at NEXT_PUBLIC_API_URL (default http://localhost:8000)
npm run lint
npm run build
```

`frontend/` is **Next.js 16** (React 19, Tailwind v4) — its `AGENTS.md` says APIs differ from older Next.js; read `frontend/node_modules/next/dist/docs/` before using unfamiliar APIs.

## Architecture

**Graph** (`backend/app/graph/builder.py`): `START → triage → {diagnostician ∥ drug_safety} → critique → (diagnostician | report) → END`.
- Diagnostician and Drug Safety run in the same LangGraph superstep; critique runs once after both. Any `ClinicalState` key written by both parallel nodes needs a reducer (`graph/state.py`); today only `agent_logs` is shared.
- Re-route: critique sets `critique.reroute_requested` and increments `reroute_count` when confidence < `critique_threshold` and `reroute_count < max_reroutes` (config). Only the Diagnostician re-runs; Drug Safety does not.

**Agents** (`backend/app/agents/`): each is `async def agent(state, ctx) -> dict` (partial state update) wrapped in `@agent_node("name")` from `agents/base.py`. The wrapper emits `agent_started` / `agent_completed` (with `duration_ms` and JSON `output`) / `agent_failed` and appends to `agent_logs`. Inside an agent, use `ctx.think(msg)` for live progress and `ctx.emit(type, **data)` for other events (the critique emits `reroute`). `attempt` is `reroute_count + 1` for diagnostician/critique, so retries show up as attempt 2.

**Streaming** (`backend/app/runs.py`, `backend/app/api.py`): `POST /api/cases` starts the graph as a background task and returns `run_id`. Events from LangGraph's `custom` stream are buffered on the `Run`. `GET /api/runs/{id}/stream` is SSE: `id:` is the event index and `data:` is JSON with a `type` field. Subscribers replay from `Last-Event-ID`, so browser `EventSource` reconnects never re-run a case. The stream always ends with a `done` event, appended atomically with `run.done = True`. Runs are in-memory only until Phase 5 (Supabase).

**Event contract** is mirrored by hand in `frontend/src/lib/types.ts` (`RunEvent`, report schemas) — keep it in sync with `backend/app/schemas.py` and `agents/base.py`. `frontend/src/hooks/useRunStream.ts` folds events into per-agent, per-attempt UI state.

**LLM** (`backend/app/services/llm.py`): agents call `get_llm().structured(system=, user=, schema=PydanticModel, budget_seconds=)`. `get_llm()` returns `None` without `GROQ_API_KEY`, and **every agent must work in that case and when `LLMError` is raised** (rules fallback). Requests use Groq strict `json_schema` mode. `strict_schema()` converts Pydantic schemas: refs inlined, all fields required, constraint keywords stripped (Pydantic still validates them, with one repair round-trip). Each call has a wall-clock budget. The primary model gets 60% of it, then `llm_fallback_model` gets the rest. Responses are cached in SQLite keyed by the request body. Agent budgets are module constants (triage 6s, drug explanations 8s) sized for the PRD's ≤15s end-to-end target. Model IDs are config: Llama 3.3 70B is not available on this Groq account.
- Groq quirks found live: Qwen + strict mode + `reasoning_format: "hidden"` produces garbage, so it uses `"parsed"`. Qwen strict mode also fails randomly with HTTP 400 `json_validate_failed`, which is treated as retryable. Free-tier 503s and 429s are frequent. httpx timeouts are per read, so the total is enforced with `asyncio.timeout`.

**Safety rules in agents:** Triage urgency = max(NEWS2, LLM). The LLM may escalate but never downgrade. Without an LLM, the red-flag phrase rules (`agents/red_flags.py`) escalate to HIGH, so classic ACS with normal vitals is never LOW. Drug interaction severity always comes from DDInter. The LLM only writes `explanation`/`clinical_action` (`explanation_source="llm"`) and only for graded pairs, never for `unknown` ones.

**Diagnostician** (`agents/diagnostician.py`) runs four steps:
1. The LLM proposes up to 5 hypotheses, each with a PubMed query. On a re-route, the critique's flags and questions are added to both prompts.
2. The case query plus every hypothesis query go to `services/retrieval.py`. That encodes them with the MedCPT query encoder, calls the `hybrid_search` SQL function per query (pgvector `<#>` inner product + full-text with OR'd terms, RRF-fused), and pools candidates round-robin capped at `rerank_pool`. The cross-encoder scores each candidate against the query that found it, and `evidence_k` abstracts are kept round-robin across queries.
3. The LLM picks the top 3 citing `[E#]` ids only. Ids not in the retrieved list are dropped in code.
4. ICD-11 codes are looked up via `services/icd.py`. WHO's `autocode` returns 500 on release 2026-01, so it uses `search` + a qualifier rule ("Dengue fever" must not become "Severe dengue"), with `autocode` on 2025-01 as the fallback.

Without an LLM there is no differential (empty list). Without retrieval, the diagnoses are uncited.
- Corpus: 50K abstracts, 130 topics, ~362 MB of the 500 MB free tier. At this size the keyword side of `hybrid_search` must stay bounded (`migrations/002`): it ANDs the terms first, falls back to OR, and ranks at most 300 matches. Ranking all OR matches (~15K rows) took ~7 s on a cold cache and silently blew the retrieval budget. Hybrid queries run in parallel on a 6-connection pool with `statement_timeout=3s`. The ingestion reconnects when the Supabase pooler drops the connection.
- Database code is **sync psycopg in `asyncio.to_thread`**, because psycopg async does not work on the Windows Proactor loop. MedCPT inference shares that thread and holds a lock. `pubmed_chunks` stores `halfvec(768)` to fit the Supabase free tier. RLS is on (the backend connects as the owner).
- CPU costs on the dev laptop (i5-1235U): article embedding ~5–8/s with length-sorted batches (unsorted padding made it 2.4/s), and the cross-encoder ~0.15 s/doc. Int8 quantization was rejected (0.94 cosine to fp32).

**Drug data** (`services/drug_graph.py`, `services/drug_names.py`): an undirected graph of 1,939 drugs and 160K pairs. Duplicate pairs across DDInter's per-ATC files keep the most severe graded level. DDInter has no mechanism text and mixes INN/USAN names (`acetylsalicylic acid` but `salbutamol`), so names resolve via exact → synonym groups → RxNorm (`approximateTerm` score ≥ 7, then ingredients, which handles brands and combination products) → fuzzy.

**Tests** (`tests/conftest.py`) set env vars before importing app modules, which override `backend/.env`: no Groq key, LLM cache, RxNorm, `DATABASE_URL` or ICD credentials, and `DDINTER_DIR=tests/fixtures/ddinter`. Tests never touch the network. Use the `fake_llm({SchemaName: response | callable | exception})` and `fake_retriever(evidence | exception)` fixtures; `make_evidence()` builds `Evidence` rows. Agents can be called directly (outside a graph run, stream events are dropped).

## Decided stack deviations from the PRD

FastAPI (not Flask), Supabase pgvector (not local Postgres), DDInter + RxNorm (not DrugBank CSV, which has no interaction data), NEWS2 (not SEWS), Drug Safety parallel with Diagnostician, MedCPT/bge-v1.5 + Postgres full-text hybrid retrieval + reranker, a Critique LLM from a different model family than the Diagnostician. Pitfalls to remember:
- Groq `llama3-70b-8192` is retired — keep model names in config.
- MIMIC-III labels are ICD-9 but the system outputs ICD-11 — eval needs mapping or name-level matching; DDXPlus is an additional eval set.
- Supabase free tier (500 MB) fits ~50K embedded abstracts, not 500K. Store one abstract per chunk.
- Drug interactions are symmetric — use an undirected graph.

## Evaluation targets (PRD §9)

Top-1 ≥65%, Top-3 ≥80%, DDI recall ≥80%, RAG precision ≥0.70, latency ≤15s (50 cases), critique calibration Pearson r ≥0.6. Plus an ablation without the Critique agent and baselines (single-LLM, rule-based). Cache LLM responses so eval runs are reproducible.
