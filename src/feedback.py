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
