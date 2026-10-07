# MedOrchestra evaluation — DDXPlus (44 cases)

1 case(s) excluded because an LLM step still failed after retries (Groq free-tier rate limits).

| Arm | Top-1 | Top-3 |
|---|---|---|
| MedOrchestra (final, with Critique) | 59% (26/44) | 73% (32/44) |
| Ablation: first pass, no Critique | 57% (25/44) | 66% (29/44) |
| Baseline: single LLM call | 55% (24/44) | 75% (33/44) |

PRD targets: Top-1 ≥65%, Top-3 ≥80%.

- **Re-routed cases:** 26/44; on those, top-3 went from 50% (13/26) (first pass) to 62% (16/26) (final)
- **Latency:** median 15.1 s, p90 19.8 s, ≤15 s: 49% (18/37); single-pass median 9.9 s
- **Retrieval precision (LLM-judged):** 72% (255/352) of retrieved abstracts relevant to their query (target ≥70%)
- **Critique calibration (proxy):** Pearson r = 0.47 between critique confidence and final top-1 correctness (target r ≥0.6 vs physician ratings)
- **Critique ran as LLM:** 100% (44/44)

Judging: qwen/qwen3.8-27b (different family from the judged gpt-oss models) — spot-check `cases.jsonl` by hand.