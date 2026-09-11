"""User-facing labels for session-debug cases."""

from __future__ import annotations


def case_badge_label(case: str | None, *, cannot_fix_reason: str | None = None) -> str:
    if case == "completed":
        return "Completed"
    if case == "already_fixed":
        return "Already fixed"
    if case == "needs_fix":
        return "Needs fix"
    if case == "cannot_fix":
        by_reason = {
            "infra": "Platform issue",
            "new_feature": "Out of scope",
            "permissions": "Credentials issue",
            "too_large": "Too large",
            "unknown": "Cannot fix",
        }
        return by_reason.get(cannot_fix_reason or "unknown", "Cannot fix")
    return "Session debug"
