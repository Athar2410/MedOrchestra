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
| Vector store / DB | Supabase Postgres + pgvector (Phase 3+) |
| Drug interactions | DDInter 2.0 → NetworkX graph, RxNorm name normalization |

## Running locally

Requires Python ≥3.11 and Node ≥20.

```powershell
# Backend (http://localhost:8000)
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pipelines.download_ddinter   # drug-interaction data, ~13 MB (slow server, ~10 min)
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# Frontend (http://localhost:3000), in a second terminal
cd frontend
npm install
npm run dev
```

Config: copy `backend/.env.example` → `backend/.env` and add a Groq API key (https://console.groq.com/keys). Without a key the app still runs, using rule-based triage and showing interactions without explanations. Frontend config (optional): `frontend/.env.example` → `frontend/.env.local`.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | End-to-end skeleton: graph with parallel Diagnostician/Drug Safety and one-shot critique re-route, NEWS2 triage, SSE API, streaming UI | ✅ done |
| 2 | Groq LLM service (strict structured output, retries, time budget, fallback model, cache); LLM triage escalation with NEWS2 floor and red-flag rule fallback; DDInter graph + RxNorm → Drug Safety with LLM explanations | ✅ done |
| 3 | Supabase pgvector + PubMed ingestion (50K abstracts), MedCPT/bge embeddings, hybrid search + reranker → Diagnostician with citations; ICD-11 coding | |
| 4 | Critique: deterministic citation checks + adversarial LLM; clarification questions drive re-retrieval | |
| 5 | Persist runs to Supabase, run history, PDF export, Langfuse tracing | |
| 6 | Evaluation: DDXPlus + MIMIC demo cases, top-1/top-3, DDI recall, latency, ablation, baselines | |

## Data sources & licences

- **DDInter 2.0** (https://ddinter2.scbdd.com) — CC BY-NC-SA 4.0: non-commercial, attribution required. Raw files are not committed; run `pipelines.download_ddinter`.
- **RxNorm / RxNav** (NLM) — public API, used for brand/misspelled drug names.
