"""Startup warmup for retrieval and answer generation."""

from __future__ import annotations

import logging
import os

from src.answer import warm_agent
from src.chunker import DATA_DIR, load_chunks
from src.retrieve import retrieve

logger = logging.getLogger(__name__)

_WARMUP_QUERY = "how do I set up Cursor?"


def _warmup_enabled() -> bool:
    raw = os.environ.get("WARM_ON_STARTUP", "1").strip().lower()
    return raw not in ("0", "false", "no")


def check_retrieval_ready() -> tuple[bool, dict[str, object]]:
    """Verify corpus load and a sample retrieval. Used by /ready and startup warmup."""
    if not DATA_DIR.is_dir():
        return False, {"status": "not_ready", "reason": "data directory missing"}

    chunks = load_chunks(DATA_DIR)
    if not chunks:
        return False, {"status": "not_ready", "reason": "no corpus chunks"}

    try:
        hits = retrieve(_WARMUP_QUERY, k=1)
    except Exception as exc:  # noqa: BLE001 — surface readiness failure to caller
        return False, {"status": "not_ready", "reason": f"retrieval failed: {exc}"}

    if not hits:
        return False, {"status": "not_ready", "reason": "retrieval returned no chunks"}

    return True, {"status": "ready", "chunks": len(chunks)}


def warm_retrieval() -> bool:
    """Warm the embedding model and corpus index. Returns True when ready."""
    ok, body = check_retrieval_ready()
    if ok:
        logger.info("Retrieval warmup ok (%s chunks)", body.get("chunks"))
    else:
        logger.warning("Retrieval warmup failed: %s", body.get("reason"))
    return ok


def warm_services() -> None:
    """Warm retrieval and optionally the Cursor agent. Never raises."""
    if not _warmup_enabled():
        return
    warm_retrieval()
    warm_agent()
