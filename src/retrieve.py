"""Public retrieve() contract consumed by ask()."""

from __future__ import annotations

from typing import Any


def retrieve(question: str, k: int = 5) -> list[dict[str, Any]]:
    """Return top-k chunks as {text, source} dicts for the answer layer."""
    try:
        from src.retriever import retrieve as semantic_retrieve

        return semantic_retrieve(question, top_k=k)
    except ImportError:
        from src.retrieve_stub import retrieve as stub_retrieve

        return stub_retrieve(question, k=k)
