"""Append query observability records for volume and gold-set triage."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.answer import is_non_answer

QUERY_LOG_PATH = Path(__file__).resolve().parent.parent / "eval" / "query_log.jsonl"
LOW_CONFIDENCE_THRESHOLD = float(os.environ.get("QUERY_LOG_LOW_CONFIDENCE", "0.35"))


def is_refused(answer: str) -> bool:
    return is_non_answer(answer)


def is_low_confidence(
    top_score: float | None,
    *,
    chunk_count: int,
    refused: bool,
) -> bool:
    """True when retrieval ran but the best match score is below threshold."""
    if refused or chunk_count == 0:
        return False
    if top_score is None:
        return True
    return top_score < LOW_CONFIDENCE_THRESHOLD


def build_record(
    *,
    question: str,
    answer: str,
    citations: list[str],
    retrieved_sources: list[str],
    top_score: float | None,
    chunk_count: int,
    channel: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    refused = is_refused(answer)
    low_confidence = is_low_confidence(
        top_score, chunk_count=chunk_count, refused=refused
    )
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": question.strip(),
        "answer_preview": answer.strip()[:240],
        "refused": refused,
        "low_confidence": low_confidence,
        "gold_set_candidate": refused or low_confidence,
        "top_score": top_score,
        "chunk_count": chunk_count,
        "citations": citations,
        "retrieved_sources": retrieved_sources,
        "channel": channel,
        "session_id": session_id,
    }


def append_query_log(record: dict[str, Any]) -> None:
    """Write one query observability record as a single JSONL line."""
    QUERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUERY_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_query(**kwargs: Any) -> None:
    """Build and append a query log record."""
    append_query_log(build_record(**kwargs))
