"""Deterministic changelog evidence for Case 1 (already fixed)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.codegraph_client import project_path
from src.scout import ScoutReport
from src.session_enrichers import EnrichmentBundle, _recent_commits
from src.session_verdict import DebugVerdict


@dataclass
class ChangelogMatch:
    ref: str
    subject: str
    date: str
    match_type: str  # error_substring | file_path
    match_detail: str

    def evidence_line(self) -> str:
        return (
            f"{self.ref}: {self.subject} "
            f"(matched {self.match_type}: {self.match_detail})"
        )


def _paths_from_text(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(
                r"(?:[\w.-]+/)*[\w.-]+\.(?:py|go|ts|tsx|js|jsx|yaml|yml|md|json)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
    )


def _error_needle(error: str) -> str:
    cleaned = re.sub(r"\s+", " ", error.strip())
    if len(cleaned) < 12:
        return ""
    # Prefer a distinctive slice — skip very generic prefixes.
    for prefix in ("ResolveMessage:", "Error:", "fatal:"):
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned[:120].lower()


def find_strong_changelog_match(
    report: ScoutReport,
    enrichment: EnrichmentBundle,
) -> ChangelogMatch | None:
    """Return a changelog match only when evidence is strong (not LLM-only)."""
    error = (report.error or "").strip()
    if not error:
        return None

    needle = _error_needle(error)
    if needle:
        for hit in enrichment.changelog_hits:
            subject = str(hit.get("subject") or "")
            if needle in subject.lower():
                return ChangelogMatch(
                    ref=str(hit.get("ref") or "?"),
                    subject=subject,
                    date=str(hit.get("date") or ""),
                    match_type="error_substring",
                    match_detail=needle[:80],
                )

    paths = _paths_from_text(error)
    root = project_path()
    if paths and root:
        for path in paths[:5]:
            hits = _recent_commits(root, paths=[path], limit=3)
            if not hits:
                continue
            hit = hits[0]
            return ChangelogMatch(
                ref=str(hit.get("ref") or "?"),
                subject=str(hit.get("subject") or ""),
                date=str(hit.get("date") or ""),
                match_type="file_path",
                match_detail=path,
            )

    return None


def apply_already_fixed_gate(
    verdict: DebugVerdict,
    report: ScoutReport,
    enrichment: EnrichmentBundle,
) -> DebugVerdict:
    """Only keep already_fixed when deterministic changelog evidence exists."""
    if verdict.case != "already_fixed":
        return verdict

    match = find_strong_changelog_match(report, enrichment)
    if match:
        evidence = [match.evidence_line(), *verdict.evidence]
        return DebugVerdict(
            case="already_fixed",
            confidence=verdict.confidence,
            summary=verdict.summary,
            root_cause=verdict.root_cause,
            evidence=evidence[:8],
            cannot_fix_reason=verdict.cannot_fix_reason,
            proposed_fix=verdict.proposed_fix,
            matching_fix_ref=match.ref,
        )

    # No strong match — do not tell the user "already fixed".
    fallback_case = "needs_fix" if verdict.proposed_fix else "cannot_fix"
    evidence = list(verdict.evidence)
    evidence.insert(
        0,
        "No strong changelog match (error substring or file path) — not treating as already fixed.",
    )
    return DebugVerdict(
        case=fallback_case,  # type: ignore[arg-type]
        confidence=max(0.0, verdict.confidence - 0.2),
        summary=verdict.summary or report.error or "Session failed",
        root_cause=verdict.root_cause or report.error or "",
        evidence=evidence[:8],
        cannot_fix_reason=verdict.cannot_fix_reason or "unknown",
        proposed_fix=verdict.proposed_fix,
        matching_fix_ref=None,
    )
