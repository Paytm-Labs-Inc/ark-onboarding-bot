"""Route user messages to onboarding Q&A or session debug."""

from __future__ import annotations

import re
from typing import Literal

# Ark session ids: s- followed by 10 alphanumeric chars (see first-run.md).
_ARK_SESSION_ID = re.compile(r"\bs-([a-z0-9]{10})\b", re.IGNORECASE)

# Explicit debug commands: "debug s-abc1234567" or "/debug s-..."
_DEBUG_PREFIX = re.compile(
    r"^(?:/debug|debug\s+session|session\s+debug)\s+(s-[a-z0-9]{10})\b",
    re.IGNORECASE,
)

Intent = Literal["onboarding", "session_debug"]


def extract_ark_session_id(text: str) -> str | None:
    """Return the first Ark session id in *text*, normalised to lowercase."""
    text = text.strip()
    prefix = _DEBUG_PREFIX.search(text)
    if prefix:
        return prefix.group(1).lower()
    match = _ARK_SESSION_ID.search(text)
    if match:
        return f"s-{match.group(1).lower()}"
    # Message is only a session id.
    bare = text.strip().lower()
    if re.fullmatch(r"s-[a-z0-9]{10}", bare):
        return bare
    return None


def resolve_intent(
    question: str,
    *,
    debug_thread: bool = False,
) -> tuple[Intent, str | None]:
    """Decide whether *question* is onboarding or session debug.

    Returns (intent, ark_session_id). *ark_session_id* is set only for debug.
    """
    session_id = extract_ark_session_id(question)
    if session_id:
        return "session_debug", session_id
    if debug_thread:
        # Follow-up in an active debug conversation (e.g. "approve", "why?").
        return "session_debug", None
    return "onboarding", None
