# MedOrchestra evaluation — DDXPlus (25 cases)

0 case(s) excluded because an LLM step still failed after retries (Groq free-tier rate limits).

| Arm | Top-1 | Top-3 |
|---|---|---|
| MedOrchestra (final, with Critique) | 68% (17/25) | 88% (22/25) |
| Ablation: first pass, no Critique | 60% (15/25) | 72% (18/25) |
| Baseline: single LLM call | 64% (16/25) | 84% (21/25) |

PRD targets: Top-1 ≥65%, Top-3 ≥80%.

- **Re-routed cases:** 14/25; on those, top-3 went from 50% (7/14) (first pass) to 79% (11/14) (final)
- **Latency:** median 14.6 s, p90 20.0 s, ≤15 s: 52% (11/21); single-pass median 10.7 s
- **Retrieval precision (LLM-judged):** 71% (145/204) of retrieved abstracts relevant to their query (target ≥70%)
- **Critique calibration (proxy):** Pearson r = 0.36 between critique confidence and final top-1 correctness (target r ≥0.6 vs physician ratings)
- **Critique ran as LLM:** 100% (25/25)

Judging: qwen/qwen3.8-27b (different family from the judged gpt-oss models) — spot-check `cases.jsonl` by hand.