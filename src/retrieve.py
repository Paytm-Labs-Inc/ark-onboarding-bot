"""Public retrieve() contract consumed by ask()."""

from __future__ import annotations

from typing import Any


def retrieve(question: str, k: int | None = None) -> list[dict[str, Any]]:
    """Return top-k chunks as {text, source} dicts for the answer layer."""
    from src.retriever import DEFAULT_TOP_K, retrieve as semantic_retrieve

    return semantic_retrieve(question, top_k=k if k is not None else DEFAULT_TOP_K)
