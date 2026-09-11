"""Route user messages to onboarding Q&A or session debug."""

from __future__ import annotations

import re
from typing import Literal

from src.session_store import extract_ark_session_id

# Explicit debug commands: "debug s-abc1234567" or "/debug s-..."
_DEBUG_PREFIX = re.compile(
    r"^(?:/debug|debug\s+session|session\s+debug)\s+(s-[a-z0-9]{8,})\b",
    re.IGNORECASE,
)

Intent = Literal["onboarding", "session_debug"]


def resolve_intent(
    question: str,
    *,
    debug_thread: bool = False,
) -> tuple[Intent, str | None]:
    """Decide whether *question* is onboarding or session debug.

    Returns (intent, ark_session_id). *ark_session_id* is set only for debug.
    """
    prefix = _DEBUG_PREFIX.search(question.strip())
    if prefix:
        return "session_debug", prefix.group(1).lower()
    session_id = extract_ark_session_id(question)
    if session_id:
        return "session_debug", session_id
    if debug_thread:
        # Follow-up in an active debug conversation (e.g. "approve", "why?").
        return "session_debug", None
    return "onboarding", None
