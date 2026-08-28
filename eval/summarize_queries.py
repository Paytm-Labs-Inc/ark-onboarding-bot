#!/usr/bin/env python3
"""Summarize query observability logs — volume, hit-rate, gold-set candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.query_log import LOW_CONFIDENCE_THRESHOLD, QUERY_LOG_PATH  # noqa: E402


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A write that failed part-way leaves a truncated line; skip it the
            # way read_feedback does rather than take the weekly triage down.
            continue
    return records


def summarize(records: list[dict]) -> dict:
    total = len(records)
    refused = sum(1 for record in records if record.get("refused"))
    low_confidence = sum(1 for record in records if record.get("low_confidence"))
    candidates = [
        record
        for record in records
        if record.get("gold_set_candidate")
    ]
    retrieved = [
        record
        for record in records
        if int(record.get("chunk_count") or 0) > 0
    ]
    confident = [
        record
        for record in retrieved
        if record.get("top_score") is not None
        and float(record["top_score"]) >= LOW_CONFIDENCE_THRESHOLD
    ]

    by_day: dict[str, int] = defaultdict(int)
    for record in records:
        ts = _parse_ts(str(record.get("ts", "")))
        key = ts.date().isoformat() if ts else "unknown"
        by_day[key] += 1

    return {
        "total_queries": total,
        "refused": refused,
        "low_confidence": low_confidence,
        "retrieval_hit_rate": round(len(confident) / len(retrieved), 3) if retrieved else 0.0,
        "refusal_rate": round(refused / total, 3) if total else 0.0,
        "low_confidence_rate": round(low_confidence / total, 3) if total else 0.0,
        "queries_by_day": dict(sorted(by_day.items())),
        "gold_set_candidates": candidates,
    }


def print_report(summary: dict) -> None:
    print("Query observability summary")
    print("=" * 72)
    print(f"Low-confidence threshold: top_score < {LOW_CONFIDENCE_THRESHOLD}")
    print(f"Total queries:            {summary['total_queries']}")
    print(f"Refused:                  {summary['refused']} ({summary['refusal_rate']:.1%})")
    print(
        "Low-confidence:           "
        f"{summary['low_confidence']} ({summary['low_confidence_rate']:.1%})"
    )
    print(f"Retrieval hit-rate:       {summary['retrieval_hit_rate']:.1%}")
    print()
    if summary["queries_by_day"]:
        print("Volume by day")
        print("-" * 72)
        for day, count in summary["queries_by_day"].items():
            print(f"  {day}: {count}")
        print()
    candidates = summary["gold_set_candidates"]
    print(f"Gold-set candidates ({len(candidates)})")
    print("-" * 72)
    if not candidates:
        print("  (none yet)")
        return
    for record in candidates:
        flags = []
        if record.get("refused"):
            flags.append("refused")
        if record.get("low_confidence"):
            flags.append("low-confidence")
        flag_text = ", ".join(flags) or "flagged"
        score = record.get("top_score")
        score_text = "n/a" if score is None else f"{float(score):.3f}"
        print(f"  [{flag_text}] score={score_text} :: {record.get('question')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=QUERY_LOG_PATH,
        help=f"Path to query log JSONL (default: {QUERY_LOG_PATH})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text report",
    )
    args = parser.parse_args(argv)

    summary = summarize(load_records(args.log))
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
