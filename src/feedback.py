"""Append demo feedback ratings to a local JSONL log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FEEDBACK_PATH = Path(__file__).resolve().parent.parent / "eval" / "feedback.jsonl"


def append_feedback(record: dict[str, Any]) -> None:
    """Write one feedback record as a single JSONL line."""
    payload = dict(record)
    payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_feedback(path: Path | None = None) -> list[dict[str, Any]]:
    """Return all feedback records, newest first. Tolerates malformed lines."""
    target = path if path is not None else FEEDBACK_PATH
    if not target.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    records.reverse()
    return records
