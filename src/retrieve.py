"""Public retrieve() contract consumed by ask()."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[dict[str, Any]]
    top_score: float | None


def retrieve_scored(
    question: str, k: int | None = None, *, use_pins: bool = True
) -> RetrievalResult:
    """Return top-k chunks and the best similarity score."""
    from src.retriever import DEFAULT_TOP_K, retrieve_scored as semantic_retrieve

    scored = semantic_retrieve(
        question, top_k=k if k is not None else DEFAULT_TOP_K, use_pins=use_pins
    )
    return RetrievalResult(chunks=list(scored.chunks), top_score=scored.top_score)


def retrieve(
    question: str, k: int | None = None, *, use_pins: bool = True
) -> list[dict[str, Any]]:
    """Return top-k chunks as {text, source} dicts for the answer layer."""
    return retrieve_scored(question, k=k, use_pins=use_pins).chunks
