# MedOrchestra

Multi-agent clinical decision support research prototype (B.Tech capstone). Four agents — **Triage**, **Diagnostician**, **Drug Safety**, **Critique** — are orchestrated with LangGraph; the Critique agent can send a low-confidence differential back to the Diagnostician once. Agent reasoning streams live to the UI over SSE.

> Research prototype on synthetic/open data. Not for clinical use.

See `MedOrchestra_PRD(1).pdf` for the original requirements.

## Stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph |
| API | FastAPI + Server-Sent Events |
| Frontend | Next.js 16 (App Router) + Tailwind v4 |
| LLM | Groq: `openai/gpt-oss-120b` (agents), `qwen/qwen3.8-27b` (critique), `openai/gpt-oss-20b` (fallback) |
| Retrieval | PubMed abstracts in Supabase Postgres (pgvector `halfvec` + full-text, RRF hybrid), MedCPT query/article encoders + cross-encoder reranker |
| Coding | WHO ICD-11 API |
| Drug interactions | DDInter 2.0 → NetworkX graph, RxNorm name normalization |

## Running locally

Requires Python ≥3.11 and Node ≥20.

```powershell
# Backend (http://localhost:8000)
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,ml]" --extra-index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python -m pipelines.download_ddinter   # drug-interaction data, ~13 MB (slow server, ~10 min)
.\.venv\Scripts\python -m pipelines.migrate            # create pubmed_chunks + hybrid_search in Supabase
.\.venv\Scripts\python -m pipelines.ingest_pubmed --limit 50000   # PubMed corpus; CPU embedding, ~2-3 h, resumable
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# Frontend (http://localhost:3000), in a second terminal
cd frontend
npm install
npm run dev
```

Config: copy `backend/.env.example` → `backend/.env` and add a Groq API key (https://console.groq.com/keys), the Supabase session-pooler `DATABASE_URL`, WHO ICD-11 API credentials and (optional) an NCBI API key. Without a key the app still runs, using rule-based triage and showing interactions without explanations. Frontend config (optional): `frontend/.env.example` → `frontend/.env.local`.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | End-to-end skeleton: graph with parallel Diagnostician/Drug Safety and one-shot critique re-route, NEWS2 triage, SSE API, streaming UI | ✅ done |
| 2 | Groq LLM service (strict structured output, retries, time budget, fallback model, cache); LLM triage escalation with NEWS2 floor and red-flag rule fallback; DDInter graph + RxNorm → Drug Safety with LLM explanations | ✅ done |
| 3 | Supabase pgvector + PubMed ingestion (130 topics incl. all DDXPlus conditions), MedCPT embeddings, hybrid search + reranker → hypothesis-driven Diagnostician with validated PubMed citations; ICD-11 coding | ✅ done |
| 4 | Critique: adversarial LLM review (different model family), cross-encoder citation verification, missed-diagnosis and clarification feedback into the re-route, likely-treatment vs current-medication cautions | ✅ done |
| 5 | Runs persisted to Supabase (case, report, full event stream), Recent cases panel with timeline replay, print-to-PDF export with case summary | ✅ done |
| 6 | Evaluation on DDXPlus (top-1/top-3, Critique ablation, single-LLM baseline, retrieval precision, latency, calibration proxy) and DDI recall via brand names — results in `backend/eval/results/` | ⏳ 25/50 cases |

## Data sources & licences

- **DDInter 2.0** (https://ddinter2.scbdd.com) — CC BY-NC-SA 4.0: non-commercial, attribution required. Raw files are not committed; run `pipelines.download_ddinter`.
- **RxNorm / RxNav** (NLM) — public API, used for brand/misspelled drug names.
- **PubMed** (NLM) via E-utilities — abstracts stored for retrieval; copyright remains with publishers, used for research retrieval only.
- **MedCPT** (NCBI, Jin et al. 2023) — query/article/cross encoders from Hugging Face.
- **ICD-11** (WHO) — ICD API, CC BY-ND 3.0 IGO.
