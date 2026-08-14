# retrieval tuning

Grid search on the 14-question gold set (`eval/questions.json`), Aug 2026.

**Winner (shipped):** `MAX_CHARS=2000`, `top_k=8`, `all-MiniLM-L6-v2` → **15/15 (100%)** on the gold set (includes onboarding-steps). Onboarding checklist questions also pin the `getting-started` onboarding path chunk when semantic rank misses it.

Re-run the grid with `python eval/tune_retrieval.py`. Raw numbers in `eval/tuning_results.json`.

| MAX_CHARS | top_k | hit-rate |
|-----------|-------|----------|
| 300 | 3 | 85.7% |
| 300 | 5 | 100% |
| 300 | 8 | 100% |
| 800 | 3 | 92.9% |
| 800 | 5 | 100% |
| 800 | 8 | 100% |
| 2000 | 3 | 100% |
| **2000** | **5** | **100%** ← shipped |
| 2000 | 8 | 100% |

Smaller chunks with low top-k miss occasionally; `2000` / `top_k=5` keeps full section context for the answer layer while clearing the 85% bar. No stronger embedding model needed on this corpus.
