"""Pre-retrieval jailbreak gate — structural patterns only, not scope/enumeration."""

from __future__ import annotations

import re
from typing import Any

from src.answer import REFUSAL_PHRASE

# Match jailbreak *shape* (override-verb near instruction-noun, etc.), not
# full eval sentences. Scope and enumeration stay with retrieval + the model.
_JAILBREAK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(ignore|disregard|forget|override)\s+(all\s+)?(prior|previous|above|your)\s+"
        r"(instructions|rules|directives|prompt)",
        r"(ignore|disregard|forget)\s+(all\s+)?(instructions|rules|directives)",
        r"forget everything above",
        r"(show|print|reveal|display|repeat|translate)\s+(me\s+)?your\s+"
        r"(system prompt|instructions|prompt)",
        r"(show|print|reveal|display)\s+(me\s+)?(the\s+)?instructions you were given",
        r"(show|print|reveal|display)\s+(me\s+)?the\s+system prompt"
        r"(?! (field|key|parameter|option|yaml))",
        r"repeat the text above",
        r"(you are now|act as|pretend you)\s+(an?\s+)?"
        r"(unrestricted|unrestricted assistant|DevGPT|FreeBot|assistant with no rules)",
        r"pretend you have no content policy",
        r"(enable|enter)\s+(developer|dev)\s+mode",
        r"disable your content rules",
        r"complete this sentence exactly",
    )
)


def _normalise(question: str) -> str:
    text = question.lower().strip()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def should_refuse(question: str) -> bool:
    """True when the question matches a jailbreak shape before retrieval."""
    text = _normalise(question)
    if not text:
        return False
    return any(pattern.search(text) for pattern in _JAILBREAK_PATTERNS)


def refusal_result() -> dict[str, Any]:
    return {
        "answer": REFUSAL_PHRASE,
        "citations": [],
        "retrieved_sources": [],
        "top_score": None,
        "chunk_count": 0,
    }
