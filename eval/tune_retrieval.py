#!/usr/bin/env python3
"""Grid-search retrieval settings against the eval gold set."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.run_eval import (  # noqa: E402
    QUESTIONS_PATH,
    evaluate_question,
    load_questions,
)
from src.chunker import MAX_CHARS as SHIPPED_MAX_CHARS  # noqa: E402
from src.retriever import DEFAULT_TOP_K as SHIPPED_TOP_K  # noqa: E402


@dataclass(frozen=True)
class TuneResult:
    max_chars: int
    top_k: int
    model_name: str
    hits: int
    total: int

    @property
    def hit_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return round(100.0 * self.hits / self.total, 1)


def _pick_winner(results: list[TuneResult]) -> TuneResult:
    """Prefer the shipped chunker/retriever defaults when hit-rate is tied."""
    max_hits = max(item.hits for item in results)
    tied = [item for item in results if item.hits == max_hits]
    shipped = next(
        (
            item
            for item in tied
            if item.max_chars == SHIPPED_MAX_CHARS and item.top_k == SHIPPED_TOP_K
        ),
        None,
    )
    if shipped is not None:
        return shipped
    return max(tied, key=lambda item: (item.top_k, item.max_chars))


def _score_gold_set(*, max_chars: int, top_k: int, model_name: str) -> TuneResult:
    import src.chunker as chunker
    import src.retriever as retriever

    chunker.MAX_CHARS = max_chars
    retriever.MODEL_NAME = model_name
    retriever._model = None
    retriever._default_index = None

    questions = load_questions(QUESTIONS_PATH)
    scored = [item for item in questions if item.get("expected_source") is not None]
    hits = 0
    for item in scored:
        result = evaluate_question(item, top_k=top_k, run_answer=False)
        if result.retrieval_hit:
            hits += 1
    return TuneResult(
        max_chars=max_chars,
        top_k=top_k,
        model_name=model_name,
        hits=hits,
        total=len(scored),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Grid-search retrieval hyperparameters")
    parser.add_argument(
        "--chunk-sizes",
        type=int,
        nargs="+",
        default=[300, 800, 2000],
        help="MAX_CHARS values to try (default: 300 800 2000)",
    )
    parser.add_argument(
        "--top-k-values",
        type=int,
        nargs="+",
        default=[3, 5, 8],
        help="top-k values to try (default: 3 5 8)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["all-MiniLM-L6-v2"],
        help="sentence-transformer model names (default: all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path(__file__).resolve().parent / "tuning_results.json",
        help="Where to write machine-readable results",
    )
    args = parser.parse_args()

    results: list[TuneResult] = []
    combos = list(product(args.chunk_sizes, args.top_k_values, args.models))
    print(f"Running {len(combos)} retrieval configs on gold set...\n", flush=True)

    for max_chars, top_k, model_name in combos:
        label = f"max_chars={max_chars} top_k={top_k} model={model_name}"
        print(f"  {label} ...", flush=True)
        result = _score_gold_set(max_chars=max_chars, top_k=top_k, model_name=model_name)
        results.append(result)
        print(f"    -> {result.hits}/{result.total} ({result.hit_rate}%)", flush=True)

    best = _pick_winner(results)
    print("\nBest config")
    print("=" * 60)
    print(
        f"MAX_CHARS={best.max_chars}, top_k={best.top_k}, "
        f"model={best.model_name} -> {best.hits}/{best.total} ({best.hit_rate}%)"
    )

    payload = {
        "winner": {
            "max_chars": best.max_chars,
            "top_k": best.top_k,
            "model_name": best.model_name,
            "hits": best.hits,
            "total": best.total,
            "hit_rate_pct": best.hit_rate,
        },
        "runs": [
            {
                "max_chars": item.max_chars,
                "top_k": item.top_k,
                "model_name": item.model_name,
                "hits": item.hits,
                "total": item.total,
                "hit_rate_pct": item.hit_rate,
            }
            for item in results
        ],
    }
    args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
