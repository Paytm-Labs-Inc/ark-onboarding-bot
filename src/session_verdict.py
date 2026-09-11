"""Shared types for session debug classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DebugCase = Literal["already_fixed", "needs_fix", "cannot_fix"]


@dataclass
class DebugVerdict:
    case: DebugCase
    confidence: float
    summary: str
    root_cause: str
    evidence: list[str]
    cannot_fix_reason: str | None = None
    proposed_fix: str | None = None
    matching_fix_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "case": self.case,
            "confidence": self.confidence,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "cannot_fix_reason": self.cannot_fix_reason,
            "proposed_fix": self.proposed_fix,
            "matching_fix_ref": self.matching_fix_ref,
        }
