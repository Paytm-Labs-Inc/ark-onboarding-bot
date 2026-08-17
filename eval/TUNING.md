# retrieval tuning

Grid search on the gold set (`eval/questions.json`), Aug 2026. **44 questions** total: **32 scored** (retrieval hit-rate) + **12 refusal** (out-of-scope prompts for answer eval).

**Winner (shipped):** `MAX_CHARS=2000`, `top_k=8`, `all-MiniLM-L6-v2` → **32/32 (100%)** retrieval hit on the scored set.

**CI gate:** `.github/workflows/eval.yml` runs `python eval/run_eval.py --quiet-retriever --only-scored` on every PR; exit non-zero if hit-rate drops below 100% on scored questions. Refusal cases run on `main` with `--full --only-refusals` when `CURSOR_API_KEY` is configured.

Re-run the grid with `python eval/tune_retrieval.py`. Raw numbers in `eval/tuning_results.json`.

| MAX_CHARS | top_k | hit-rate |
|-----------|-------|----------|
| 300 | 3 | 87.5% |
| 300 | 5 | 100% |
| 300 | 8 | 100% |
| 800 | 3 | 93.8% |
| 800 | 5 | 100% |
| 800 | 8 | 100% |
| 2000 | 3 | 100% |
| 2000 | 5 | 100% |
| **2000** | **8** | **100%** ← shipped |

Smaller chunks with low top-k miss occasionally; `2000` / `top_k=8` keeps full section context for the answer layer while clearing the gold set. No stronger embedding model needed on this corpus.
