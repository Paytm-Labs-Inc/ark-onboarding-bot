#!/usr/bin/env python3
"""Compare blocking vs streaming answer latency for demo numbers."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from .env (fallback if python-dotenv missing)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def _cursor_key_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("CURSOR_API_KEY"):
            lines.append(line.strip())
    return lines


def _ensure_cursor_api_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH, override=True)
    except ImportError:
        pass
    _load_env_file(ENV_PATH)
    return os.environ.get("CURSOR_API_KEY", "").strip()


def _print_env_help() -> None:
    print("CURSOR_API_KEY is not set.", file=sys.stderr)
    print(f"Looked for: {ENV_PATH}", file=sys.stderr)
    if not ENV_PATH.is_file():
        print(
            "\nNo .env file found. Create one:\n"
            "  cp .env.example .env\n"
            "  nano .env\n"
            "Add exactly one line:\n"
            "  CURSOR_API_KEY=crsr_your_key_here",
            file=sys.stderr,
        )
        return

    matches = _cursor_key_lines(ENV_PATH)
    print(f"\nFound .env ({ENV_PATH.stat().st_size} bytes).", file=sys.stderr)
    if not matches:
        print(
            "But there is no CURSOR_API_KEY= line in it.\n"
            "Add:\n"
            "  CURSOR_API_KEY=crsr_your_key_here",
            file=sys.stderr,
        )
        return

    print(f"CURSOR_API_KEY appears {len(matches)} time(s) in .env:", file=sys.stderr)
    for index, line in enumerate(matches, start=1):
        _, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            print(f"  {index}. set (length {len(value)})", file=sys.stderr)
        else:
            print(f"  {index}. EMPTY — no value after =", file=sys.stderr)

    if len(matches) > 1:
        print(
            "\nKeep only ONE CURSOR_API_KEY line (delete the others).\n"
            "The last line usually wins, so an empty second line breaks loading.",
            file=sys.stderr,
        )
    elif matches and not matches[0].partition("=")[2].strip().strip('"').strip("'"):
        print("\nYour CURSOR_API_KEY line has no value after =.", file=sys.stderr)

    print(
        "\nQuick test without editing .env again:\n"
        "  export CURSOR_API_KEY=crsr_your_key_here\n"
        "  PYTHONPATH=. .venv/bin/python eval/stream_latency_demo.py",
        file=sys.stderr,
    )

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
    stream_mode = "unknown"

    for event in stream_answer(question, chunks):
        if event.get("type") == "delta":
            delta_count += 1
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - start) * 1000
        elif event.get("type") == "done":
            answer_text = str(event.get("answer", ""))
            stream_mode = str(event.get("stream_mode", "unknown"))

    total_ms = (time.perf_counter() - start) * 1000
    return {
        "mode": "streaming",
        "time_to_first_token_ms": first_token_ms if first_token_ms is not None else total_ms,
        "total_ms": total_ms,
        "delta_count": delta_count,
        "answer_chars": len(answer_text),
        "stream_mode": stream_mode,
    }


def main() -> int:
    api_key = _ensure_cursor_api_key()
    if not api_key:
        _print_env_help()
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
    if streaming["delta_count"] == 0:
        print(
            "\nNote: delta_count=0 means no incremental tokens were streamed.",
            file=sys.stderr,
        )
    print(f"\nStream mode: {streaming.get('stream_mode', 'unknown')}")
    if streaming.get("stream_mode") == "blocking-chunked":
        print(
            "Used blocking fallback with simulated chunks (text appears after full generation). "
            "Install/update the `agent` CLI on PATH for live CLI streaming.",
            file=sys.stderr,
        )
    elif streaming["delta_count"] == 0:
        print(
            "Pull the latest branch and ensure `agent` is on PATH: agent status",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
