"""Semantic retrieval over the onboarding corpus via sentence-transformers."""

from __future__ import annotations

import math
import os
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
    r"\b(steps?|how\s+(?:do\s+i|to))\b.*\b(onboa?rd(?:ing)?|get\s+started)\b|"
    r"\bonboa?rding\s+(?:steps?|path|order|checklist)\b|"
    r"\bwhat\s+(?:is|are)\s+the\s+(?:steps?|order)\b.*\bonboa?rd"
)

PINNED_MARKERS = (
    "## Start here",
    "Four steps stand between a new account and a session",
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

ENROLL_HOST_RE = re.compile(
    r"(?i)"
    r"\b(?:how\s+(?:do\s+i|to)\s+)?(?:enroll|enrole|enrol|register)\b.*\b(?:host|compute|machine|box)\b|"
    r"\b(?:host|compute|machine|box)\b.*\b(?:enroll|enrole|enrol|register)\b|"
    r"\benroll\s+(?:a\s+)?host\b"
)

ENROLL_PINNED_MARKERS = (
    "ark host enroll",
    "One-command enrollment",
    "not able to enroll compute",
    "enroll a machine for themselves",
)

WORKSPACE_RE = re.compile(
    r"(?i)"
    r"\bwhat\s+is\s+a\s+workspace\b|"
    r"\bsteps?\b.*\b(?:create|set up|setup|apply|wire|define)\b.*\bworkspace\b|"
    r"\b(?:create|set up|setup|apply|wire|define)\b.*\bworkspace\b|"
    r"\bworkspace\b.*\b(?:yaml|apply|create)\b|"
    r"\b(?:use|using)\b.*\bworkspace\b|"
    r"\bsomeone else'?s?\b.*\bworkspace\b|"
    r"\bown workspace\b|"
    r"\bshare\b.*\bworkspace\b"
)

WORKSPACE_PINNED_MARKERS = (
    "**Step 2 - Wire the workspace.**",
    "ark workspace apply",
    "**Workspace** is a full environment",
    "Workspace not found.",
    "start my myteam-review flow on the myteam-workspace workspace",
)

CURSOR_RE = re.compile(
    r"(?i)"
    r"\b(?:how\s+(?:do\s+i|to)\s+)?(?:access|use|set\s+up|setup|connect|get)\b.*\bcursor\b|"
    r"\bcursor\b.*\b(?:access|mcp|setup|set\s+up|connect|working|ark)\b|"
    r"\bwhere\b.*\b(?:put|add)\b.*\b(?:mcp|config)\b.*\bcursor\b|"
    r"\b(?:get|getting)\b.*\bcursor\b.*\b(?:working|ark)\b|"
    r"\bcan\s+(?:we|i)\s+use\s+cursor\b"
)

CURSOR_PINNED_MARKERS = (
    "# Set up Cursor",
    "Add the ark MCP server to Cursor",
    ".cursor/mcp.json",
)

MCP_TOOLS_FAIL_RE = re.compile(
    r"(?i)tools\s+fetch\s+failed|ark\s+connected\b.*failed"
)

MCP_TOOLS_FAIL_PINNED_MARKERS = (
    "tools fetch failed",
)

WRONG_TENANT_RE = re.compile(
    r"(?i)"
    r"\b(?:can'?t|cannot|not able to)\s+see\b.*\b(?:flow|machine|compute)|"
    r"\bwrong tenant\b|"
    r"\benrolled\b.*\b(?:can'?t|cannot|not)\s+see\b"
)

WRONG_TENANT_PINNED_MARKERS = (
    "I am not able to see my compute / flows anywhere",
    "wrong tenant or team",
)

JIRA_MCP_VPN_RE = re.compile(
    r"(?i)"
    r"\bjira\b.*\bmcp\b|"
    r"\bbitbucket\b.*\bmcp\b|"
    r"\bmcp\b.*\b(?:blocked|vpn|pod)|"
    r"\bpods?\b.*\bnot on vpn\b"
)

JIRA_MCP_VPN_PINNED_MARKERS = (
    "Jira / Bitbucket MCP is blocked from Ark",
    "pods are not on VPN",
)

ONBOARDING_PINNED_MARKERS = PINNED_MARKERS

USAGE_PINNED_MARKERS = (
    "**Step 1 - Define your agents.**",
    "**Step 2 - Wire the workspace.**",
    "**Step 3 - Register your flow",
    "**Step 2 - Dispatch.**",
    "session_lifecycle(op='start'",
)


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._\-/]*")
_SUBTOKEN_RE = re.compile(r"[._\-/]")


def _tokens(text: str) -> list[str]:
    """Lexical tokens, keeping identifiers whole and also split.

    Support questions are full of exact strings -- mcp.json, ark host enroll,
    session_lifecycle -- which is where dense embeddings are weakest. Keep
    `mcp.json` as one token and also emit `mcp` and `json`, so both the exact
    identifier and its parts can match.
    """
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        out.append(tok)
        # A path-prefixed identifier is also reachable as its last segment,
        # so `.cursor/mcp.json` in a page matches `mcp.json` in a question.
        if "/" in tok:
            out.append(tok.rsplit("/", 1)[-1])
        parts = [p for p in _SUBTOKEN_RE.split(tok) if p and p != tok]
        out.extend(parts)
    return out


class _BM25:
    """Okapi BM25 over the chunk texts. Small corpus, plain Python, no dependency."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.doc_lens = np.array([len(d) for d in docs], dtype=np.float32)
        self.avgdl = float(self.doc_lens.mean()) if len(docs) else 0.0
        self.tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for d in docs:
            counts: dict[str, int] = {}
            for t in d:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.tf), dtype=np.float32)
        if not self.tf:
            return out
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, counts in enumerate(self.tf):
                f = counts.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_lens[i] / self.avgdl)
                out[i] += idf * f * (self.k1 + 1) / denom
        return out


def _rrf(*rankings: np.ndarray, k: int = 60) -> np.ndarray:
    """Reciprocal-rank fusion: each list votes 1/(k+rank) for every document."""
    fused = np.zeros(max(len(r) for r in rankings), dtype=np.float32)
    for order in rankings:
        for rank, doc in enumerate(order):
            fused[int(doc)] += 1.0 / (k + rank + 1)
    return fused


_DENSE_VOTES = 2
_LEXICAL_VOTERS = 100


def hybrid_enabled() -> bool:
    """ASK_HYBRID=0 falls back to dense-only; the eval compares both."""
    return os.environ.get("ASK_HYBRID", "1").strip().lower() not in ("0", "false", "no")


@dataclass(frozen=True)
class Index:
    chunks: list[Chunk]
    embeddings: np.ndarray
    bm25: "_BM25 | None" = None


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
    bm25 = _BM25([_tokens(c["text"]) for c in chunks])
    return Index(chunks=list(chunks), embeddings=embeddings, bm25=bm25)


def _expand_query(question: str) -> str:
    if ONBOARDING_STEPS_RE.search(question):
        return (
            f"{question} onboarding path checklist full steps order getting started"
        )
    if USAGE_RE.search(question):
        return (
            f"{question} create workspace register flow start session dispatch agent"
        )
    if ENROLL_HOST_RE.search(question):
        return f"{question} ark host enroll member key API token compute"
    if WORKSPACE_RE.search(question):
        return f"{question} ark workspace apply YAML wire repos secrets actions"
    if CURSOR_RE.search(question):
        return (
            f"{question} set up cursor mcp.json ark MCP server API key getting access"
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
    use_pins: bool = True,
) -> ScoredRetrieval:
    """Return top_k chunks plus the best similarity score for observability.

    use_pins=False bypasses the hand-written layer -- both the query-expansion
    regexes and the pinned-marker rules -- so the eval can report what the
    retriever scores on its own. Production always runs with it on.
    """
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
    search_query = _expand_query(question.strip()) if use_pins else question.strip()
    query = _embed([search_query])[0]
    sims = index.embeddings @ query
    k = min(top_k, len(index.chunks))
    dense_order = np.argsort(-sims, kind="stable")
    if hybrid_enabled() and index.bm25 is not None:
        # Dense catches paraphrase and typos; BM25 catches the exact identifiers
        # and error strings support questions are made of. Fuse by rank so
        # neither score scale dominates.
        lexical = index.bm25.scores(_tokens(question))
        # Only the strongest lexical matches vote; on the gold questions
        # 339-429 of 437 chunks score non-zero from stopwords alone, and a rank
        # among those is noise. Measured: 50 voters lost one prose question's
        # page (34->33), 100 keeps every page at the same MRR.
        lexical_order = np.argsort(-lexical, kind="stable")[: _LEXICAL_VOTERS]
        # Dense votes twice. Measured on the gold set: equal weight pulled one
        # prose question's page out of the top 8 (BM25 rewarding "CI",
        # "pipeline", "team"); 2:1 keeps every page and lifts chunk MRR
        # 0.865 -> 0.887 with pins, 0.750 -> 0.796 without.
        fused = _rrf(*([dense_order] * _DENSE_VOTES), lexical_order)
        order = np.argsort(-fused, kind="stable")[:k]
    else:
        order = dense_order[:k]
    results = [index.chunks[int(i)] for i in order]
    if not use_pins:
        pass
    elif ONBOARDING_STEPS_RE.search(question):
        results = _pin_markers(
            results, index, ONBOARDING_PINNED_MARKERS, top_k=top_k
        )
    if use_pins and USAGE_RE.search(question):
        results = _pin_markers(results, index, USAGE_PINNED_MARKERS, top_k=top_k)
    if use_pins and ENROLL_HOST_RE.search(question):
        results = _pin_markers(results, index, ENROLL_PINNED_MARKERS, top_k=top_k)
    if use_pins and WORKSPACE_RE.search(question):
        results = _pin_markers(results, index, WORKSPACE_PINNED_MARKERS, top_k=top_k)
    if use_pins and CURSOR_RE.search(question):
        results = _pin_markers(results, index, CURSOR_PINNED_MARKERS, top_k=top_k)
    if use_pins and MCP_TOOLS_FAIL_RE.search(question):
        results = _pin_markers(
            results, index, MCP_TOOLS_FAIL_PINNED_MARKERS, top_k=top_k
        )
    if use_pins and WRONG_TENANT_RE.search(question):
        results = _pin_markers(
            results, index, WRONG_TENANT_PINNED_MARKERS, top_k=top_k
        )
    if use_pins and JIRA_MCP_VPN_RE.search(question):
        results = _pin_markers(
            results, index, JIRA_MCP_VPN_PINNED_MARKERS, top_k=top_k
        )
    elapsed_ms = (time.perf_counter() - start) * 1000
    top_score = float(sims.max())  # best cosine regardless of how the fusion ordered things
    print(f"retrieved {len(results)} chunks in {elapsed_ms:.0f}ms, top_score={top_score:.3f}")
    return ScoredRetrieval(chunks=results, top_score=top_score)


def retrieve(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    index: Index | None = None,
    data_dir: Path = DATA_DIR,
    use_pins: bool = True,
) -> list[Chunk]:
    """Return the top_k corpus chunks most relevant to question."""
    return retrieve_scored(
        question, top_k=top_k, index=index, data_dir=data_dir, use_pins=use_pins
    ).chunks
