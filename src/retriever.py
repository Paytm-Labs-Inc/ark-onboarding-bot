"""Semantic retrieval over the onboarding corpus via sentence-transformers."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.chunker import DATA_DIR, Chunk, load_chunks

MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_TOP_K = 8

_model = None
_default_index: "Index | None" = None

ONBOARDING_STEPS_RE = re.compile(
    r"(?i)"
    r"\b(steps?|how\s+(?:do\s+i|to))\b.*\b(onboard(?:ing)?|get\s+started)\b|"
    r"\bonboarding\s+(?:steps?|path|order|checklist)\b|"
    r"\bwhat\s+(?:is|are)\s+the\s+(?:steps?|order)\b.*\bonboard"
)

PINNED_MARKERS = (
    "## Onboarding path",
    "correct onboarding order to follow",
)

USAGE_RE = re.compile(
    r"(?i)"
    r"\bhow\s+(?:do\s+i\s+)?use\b.*\bark\b|"
    r"\busing\s+ark\b|"
    r"\bonce\s+onboarding\s+is\s+done\b|"
    r"\bafter\s+onboarding\b|"
    r"\bhow\s+to\s+(?:run|start|dispatch)\b.*\b(?:session|flow|workspace)\b|"
    r"\bwhat\s+(?:do\s+i|should\s+i)\s+do\s+(?:next|after)\b"
)

ONBOARDING_PINNED_MARKERS = PINNED_MARKERS

USAGE_PINNED_MARKERS = (
    "**Step 1 - Define your agents.**",
    "**Step 2 - Wire the workspace.**",
    "**Step 3 - Register your flow",
    "**Step 2 - Dispatch.**",
    "session_lifecycle(op='start'",
    "How to use Ark after onboarding",
    "How to run Ark — discover flows",
)


@dataclass(frozen=True)
class Index:
    chunks: list[Chunk]
    embeddings: np.ndarray


@dataclass(frozen=True)
class ScoredRetrieval:
    chunks: list[Chunk]
    top_score: float | None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers not installed; run "
                "pip install -r requirements.txt"
            ) from exc
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _embed(texts: list[str]) -> np.ndarray:
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)


def build_index(chunks: list[Chunk]) -> Index:
    """Embed all chunk texts into an L2-normalized matrix."""
    if not chunks:
        return Index(chunks=[], embeddings=np.zeros((0, 0), dtype=np.float32))
    embeddings = _embed([c["text"] for c in chunks])
    return Index(chunks=list(chunks), embeddings=embeddings)


def _expand_query(question: str) -> str:
    if ONBOARDING_STEPS_RE.search(question):
        return (
            f"{question} onboarding path checklist full steps order getting started"
        )
    if USAGE_RE.search(question):
        return (
            f"{question} create workspace register flow start session dispatch agent"
        )
    return question


def _chunk_key(chunk: Chunk) -> str:
    return f"{chunk['source']}::{chunk['text'][:96]}"


def _pin_markers(
    results: list[Chunk],
    index: Index,
    markers: tuple[str, ...],
    *,
    top_k: int,
) -> list[Chunk]:
    pinned: list[Chunk] = []
    for chunk in index.chunks:
        if any(marker in chunk["text"] for marker in markers):
            pinned.append(chunk)

    out: list[Chunk] = []
    seen_keys: set[str] = set()
    for chunk in pinned + results:
        key = _chunk_key(chunk)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(chunk)
    return out[: max(top_k, len(pinned))]


def retrieve_scored(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    index: Index | None = None,
    data_dir: Path = DATA_DIR,
) -> ScoredRetrieval:
    """Return top_k chunks plus the best similarity score for observability."""
    global _default_index
    if not question or not question.strip():
        return ScoredRetrieval(chunks=[], top_score=None)
    if index is None:
        if _default_index is None:
            _default_index = build_index(load_chunks(data_dir))
        index = _default_index
    if not index.chunks:
        return ScoredRetrieval(chunks=[], top_score=None)

    start = time.perf_counter()
    search_query = _expand_query(question.strip())
    query = _embed([search_query])[0]
    sims = index.embeddings @ query
    k = min(top_k, len(index.chunks))
    order = np.argsort(-sims, kind="stable")[:k]
    results = [index.chunks[int(i)] for i in order]
    if ONBOARDING_STEPS_RE.search(question):
        results = _pin_markers(
            results, index, ONBOARDING_PINNED_MARKERS, top_k=top_k
        )
    if USAGE_RE.search(question):
        results = _pin_markers(results, index, USAGE_PINNED_MARKERS, top_k=top_k)
    elapsed_ms = (time.perf_counter() - start) * 1000
    top_score = float(sims[int(order[0])])
    print(f"retrieved {len(results)} chunks in {elapsed_ms:.0f}ms, top_score={top_score:.3f}")
    return ScoredRetrieval(chunks=results, top_score=top_score)


def retrieve(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    index: Index | None = None,
    data_dir: Path = DATA_DIR,
) -> list[Chunk]:
    """Return the top_k corpus chunks most relevant to question."""
    return retrieve_scored(
        question, top_k=top_k, index=index, data_dir=data_dir
    ).chunks
