#!/usr/bin/env python3
"""Compare blocking vs streaming answer latency for demo numbers."""

from __future__ import annotations

import os
import sys
import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from src.answer import answer, stream_answer
from src.ask import clear_answer_cache, clear_retrieval_cache
from src.retrieve import retrieve_scored

QUESTION = "how do I set up Cursor with Ark MCP?"


def measure_blocking(question: str) -> dict[str, float | str]:
    clear_retrieval_cache()
    clear_answer_cache()
    scored = retrieve_scored(question, k=8)
    chunks = scored.chunks

    start = time.perf_counter()
    result = answer(question, chunks)
    total_ms = (time.perf_counter() - start) * 1000
    return {
        "mode": "blocking",
        "time_to_first_token_ms": total_ms,
        "total_ms": total_ms,
        "answer_chars": len(str(result.get("answer", ""))),
    }


def measure_streaming(question: str) -> dict[str, float | str]:
    clear_retrieval_cache()
    clear_answer_cache()
    scored = retrieve_scored(question, k=8)
    chunks = scored.chunks

    start = time.perf_counter()
    first_token_ms: float | None = None
    delta_count = 0
    answer_text = ""

    for event in stream_answer(question, chunks):
        if event.get("type") == "delta":
            delta_count += 1
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - start) * 1000
        elif event.get("type") == "done":
            answer_text = str(event.get("answer", ""))

    total_ms = (time.perf_counter() - start) * 1000
    return {
        "mode": "streaming",
        "time_to_first_token_ms": first_token_ms if first_token_ms is not None else total_ms,
        "total_ms": total_ms,
        "delta_count": delta_count,
        "answer_chars": len(answer_text),
    }


def main() -> int:
    if not os.environ.get("CURSOR_API_KEY", "").strip():
        print("CURSOR_API_KEY is not set.", file=sys.stderr)
        return 1

    print(f"Question: {QUESTION}\n")

    blocking = measure_blocking(QUESTION)
    print("Blocking (before):")
    for key, value in blocking.items():
        print(f"  {key}: {value}")

    streaming = measure_streaming(QUESTION)
    print("\nStreaming (after):")
    for key, value in streaming.items():
        print(f"  {key}: {value}")

    improvement = (
        1 - (streaming["time_to_first_token_ms"] / blocking["time_to_first_token_ms"])
    ) * 100
    print(
        f"\nTime-to-first-token improvement: {improvement:.1f}% "
        f"({blocking['time_to_first_token_ms']:.0f}ms -> "
        f"{streaming['time_to_first_token_ms']:.0f}ms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
