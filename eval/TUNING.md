# retrieval tuning

Grid search on the gold set (`eval/questions.json`), Aug 2026. **44 questions** total: **32 scored** (retrieval hit-rate) + **12 refusal** (out-of-scope prompts for answer eval).

**Winner (this grid, 2026-08-20):** `MAX_CHARS=2000`, `top_k=8`, `all-MiniLM-L6-v2` → **32/32 (100%)** retrieval hit on the scored set.

> **Superseded 2026-08-27:** shipped `MAX_CHARS=900`. The grid never measured encoder truncation: at 2000, 44.6% of chunks exceeded the model's 256-token window. Hit@8 is the same at 900 once labels name every page that answers a question (#46); chunk-level MRR (#40) is the metric to tune against from here.

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
| **2000** | **8** | **100%** ← this grid's winner (superseded, see note above) |

Smaller chunks with low top-k missed occasionally on this grid, which never measured encoder truncation. Shipped since 2026-08-27: `900` / `top_k=8`, with every piece of a split section carrying its heading. Judge encoder changes on chunk-level MRR (#40), not this table.

## Do the pin rules still earn their place? Measured 2026-08-28

The plan after the BM25 + dense hybrid (#52) was to delete the hand-written pin and
query-expansion rules in `src/retriever.py` once the retriever could stand on its own,
with the bar set at **+0.12 chunk MRR**. Measured on the 35-row scored set at `main`
(`2cbc26a`), `MAX_CHARS=900`, `top_k=8`:

| | retrieval hit@8 | chunk recall@8 | chunk MRR |
|---|---|---|---|
| with pins (shipped) | 35/35 | 35/35 | **0.890** |
| `--no-pins` | 34/35 | 34/35 | 0.796 |

**Verdict: keep the pins.** They are worth **+0.094 MRR** and one whole question
(`post-onboarding-usage`, "how to use ark once onboarding is done", which without them
retrieves `getting-access` / `updating-cli-plugin` / `roadmap` instead of
`authoring-your-own`). The hybrid did lift the unpinned baseline — 0.771 before #52,
0.796 after — but +0.025 is a fifth of what deleting the rules would cost, so the
+0.12 bar is nowhere near met.

Re-run with `python eval/run_eval.py --only-scored --quiet-retriever` and again with
`--no-pins`. Worth repeating after the FAQ corpus dedupe and after any encoder change
(Tier 2: BGE-M3 + a cross-encoder reranker), since either could close the gap.
