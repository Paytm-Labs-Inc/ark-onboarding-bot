"""Semantic retrieval over the onboarding corpus via sentence-transformers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.chunker import DATA_DIR, Chunk, load_chunks

MODEL_NAME = "all-MiniLM-L6-v2"

_model = None
_default_index: "Index | None" = None


@dataclass(frozen=True)
class Index:
    chunks: list[Chunk]
    embeddings: np.ndarray


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


def retrieve(
    question: str,
    *,
    top_k: int = 5,
    index: Index | None = None,
    data_dir: Path = DATA_DIR,
) -> list[Chunk]:
    """Return the top_k corpus chunks most relevant to question."""
    global _default_index
    if not question or not question.strip():
        return []
    if index is None:
        if _default_index is None:
            _default_index = build_index(load_chunks(data_dir))
        index = _default_index
    if not index.chunks:
        return []

    start = time.perf_counter()
    query = _embed([question])[0]
    sims = index.embeddings @ query
    k = min(top_k, len(index.chunks))
    order = np.argsort(-sims, kind="stable")[:k]
    results = [index.chunks[int(i)] for i in order]
    elapsed_ms = (time.perf_counter() - start) * 1000
    top_score = float(sims[int(order[0])])
    print(f"retrieved {k} chunks in {elapsed_ms:.0f}ms, top_score={top_score:.3f}")
    return results
