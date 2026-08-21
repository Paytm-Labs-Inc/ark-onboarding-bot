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
    r"\b(steps?|how\s+(?:do\s+i|to))\b.*\b(onboa?rd(?:ing)?|get\s+started)\b|"
    r"\bonboa?rding\s+(?:steps?|path|order|checklist)\b|"
    r"\bwhat\s+(?:is|are)\s+the\s+(?:steps?|order)\b.*\bonboa?rd"
)

PINNED_MARKERS = (
    "## Onboarding path",
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
    "Workspaces are repos plus tools",
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
    "Cursor Settings - > MCP",
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
    if ENROLL_HOST_RE.search(question):
        results = _pin_markers(results, index, ENROLL_PINNED_MARKERS, top_k=top_k)
    if WORKSPACE_RE.search(question):
        results = _pin_markers(results, index, WORKSPACE_PINNED_MARKERS, top_k=top_k)
    if CURSOR_RE.search(question):
        results = _pin_markers(results, index, CURSOR_PINNED_MARKERS, top_k=top_k)
    if MCP_TOOLS_FAIL_RE.search(question):
        results = _pin_markers(
            results, index, MCP_TOOLS_FAIL_PINNED_MARKERS, top_k=top_k
        )
    if WRONG_TENANT_RE.search(question):
        results = _pin_markers(
            results, index, WRONG_TENANT_PINNED_MARKERS, top_k=top_k
        )
    if JIRA_MCP_VPN_RE.search(question):
        results = _pin_markers(
            results, index, JIRA_MCP_VPN_PINNED_MARKERS, top_k=top_k
        )
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
