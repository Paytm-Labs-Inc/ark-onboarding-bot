"""Keyword retrieval fallback until semantic retriever lands on main."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.stub_chunks import STUB_CHUNKS

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STOPWORDS = {
    "the",
    "and",
    "for",
    "how",
    "what",
    "when",
    "where",
    "who",
    "why",
    "can",
    "you",
    "your",
    "with",
    "from",
    "that",
    "this",
    "are",
    "does",
    "have",
    "about",
    "only",
    "not",
    "any",
    "all",
    "use",
    "get",
}
MIN_OVERLAP = 2


def _tokenize(text: str) -> set[str]:
    words = {word.lower() for word in re.findall(r"[a-z0-9]+", text)}
    return {word for word in words if len(word) > 2 and word not in STOPWORDS}


def _load_corpus_chunks() -> list[dict[str, str]]:
    files = sorted(DATA_DIR.glob("*.md"))
    if not files:
        return [dict(chunk) for chunk in STUB_CHUNKS]

    chunks: list[dict[str, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or not lines[0].startswith("Source:"):
            continue
        url = lines[0].split("Source:", 1)[1].strip()
        label = f"{path.stem} -- {url}"
        body = "\n".join(lines[1:]).strip()
        for section in re.split(r"(?m)^(?=## )", body):
            section = section.strip()
            if section:
                chunks.append({"source": label, "text": section})
    return chunks or [dict(chunk) for chunk in STUB_CHUNKS]


def retrieve(question: str, k: int = 5) -> list[dict[str, Any]]:
    """Return up to k chunks ranked by simple keyword overlap."""
    tokens = _tokenize(question)
    if not tokens:
        return []

    min_overlap = MIN_OVERLAP if len(tokens) >= MIN_OVERLAP else 1

    scored: list[tuple[int, dict[str, str]]] = []
    for chunk in _load_corpus_chunks():
        overlap = len(tokens & _tokenize(chunk["text"]))
        if overlap >= min_overlap:
            scored.append((overlap, chunk))

    scored.sort(key=lambda item: (-item[0], item[1]["source"]))
    return [chunk for _, chunk in scored[:k]]
