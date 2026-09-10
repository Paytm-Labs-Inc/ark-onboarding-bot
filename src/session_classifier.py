"""Classify a debugged session into one of three cases."""

from __future__ import annotations

from src.llm_json import completion_json
from src.scout import ScoutReport
from src.session_changelog_evidence import apply_already_fixed_gate
from src.session_enrichers import EnrichmentBundle
from src.session_verdict import DebugCase, DebugVerdict


_CLASSIFIER_PROMPT = """You triage failed Ark platform sessions into exactly one case.

Cases:
1. already_fixed — ONLY when changelog shows the same error text or file path was fixed on main.
2. needs_fix — a bounded code/config bug (roughly <=10 files) that Ark should dispatch to fix.
3. cannot_fix — new feature, infra/permissions, or scope too large; last resort only.

Suggest already_fixed when changelog hints at a fix, but the server requires deterministic
match evidence before showing that case to the user. Use cannot_fix only when truly blocked.

Respond with JSON only:
{{
  "case": "already_fixed" | "needs_fix" | "cannot_fix",
  "confidence": 0.0-1.0,
  "summary": "one sentence for the user",
  "root_cause": "technical root cause",
  "evidence": ["bullet 1", "bullet 2"],
  "cannot_fix_reason": null or "new_feature"|"infra"|"permissions"|"too_large"|"unknown",
  "proposed_fix": null or "concrete fix plan for needs_fix",
  "matching_fix_ref": null or "commit/PR ref for already_fixed"
}}

## Scout report
{scout}

## Onboarding docs (RAG)
{rag}

## Codebase hints
{code}

## Changelog / worktree
{changelog}
"""


def _is_provisioning_failure(report: ScoutReport, lower: str) -> bool:
    """True when the session died before agent work — compute/workspace prep."""
    if "provisioning did not complete" in lower:
        return True
    if "compute never became ready" in lower:
        return True
    if "workspace test" in lower and report.stage == "smoke":
        return True
    if report.stage == "smoke" and any(
        phrase in lower
        for phrase in (
            "did not complete within",
            "pod that cannot schedule",
            "volume attachment",
            "readwriteonce",
        )
    ):
        return True
    return False


def _heuristic_verdict(report: ScoutReport) -> DebugVerdict | None:
    """Rule-based triage when the LLM is unavailable or unnecessary."""
    err = (report.error or "").strip()
    lower = err.lower()
    if not err:
        return None
    if "cannot find module" in lower and ("ark-darwin" in lower or "/$bunfs/" in lower):
        return DebugVerdict(
            case="cannot_fix",
            confidence=0.9,
            summary="Session failed during Ark runtime startup before any agent work ran.",
            root_cause=err,
            evidence=[
                f"Failed at stage {report.stage or 'unknown'}",
                "Error originates inside the Ark executor bundle (arkd), not your repo",
            ],
            cannot_fix_reason="infra",
        )
    if _is_provisioning_failure(report, lower):
        return DebugVerdict(
            case="cannot_fix",
            confidence=0.95,
            summary=(
                "Session failed during workspace/compute provisioning before any agent work ran."
            ),
            root_cause=err,
            evidence=[
                f"Failed at stage {report.stage or 'unknown'}",
                "Compute or workspace prep did not finish — not a repo code bug",
                "Re-dispatch, check compute health, or ask #foundry-users if a PVC is stuck",
            ],
            cannot_fix_reason="infra",
        )
    return None


def classify_session(report: ScoutReport, enrichment: EnrichmentBundle) -> DebugVerdict:
    heuristic = _heuristic_verdict(report)
    if heuristic:
        return heuristic

    prompt = _CLASSIFIER_PROMPT.format(
        scout=report.to_context_blob()[:12000],
        rag=enrichment.rag_text()[:4000] or "(none)",
        code=enrichment.code_text()[:4000] or "(none)",
        changelog=(enrichment.changelog_text() + "\n" + enrichment.worktree_summary)[:4000]
        or "(none)",
    )
    try:
        raw = completion_json(prompt)
    except Exception as exc:  # noqa: BLE001
        return DebugVerdict(
            case="cannot_fix",
            confidence=0.3,
            summary=f"Could not classify session automatically: {exc}",
            root_cause=report.error or "unknown",
            evidence=["classifier failed"],
            cannot_fix_reason="unknown",
        )

    case = str(raw.get("case", "cannot_fix"))
    if case not in ("already_fixed", "needs_fix", "cannot_fix"):
        case = "cannot_fix"

    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]

    verdict = DebugVerdict(
        case=case,  # type: ignore[arg-type]
        confidence=float(raw.get("confidence") or 0.5),
        summary=str(raw.get("summary") or ""),
        root_cause=str(raw.get("root_cause") or report.error or ""),
        evidence=[str(e) for e in evidence[:8]],
        cannot_fix_reason=raw.get("cannot_fix_reason"),
        proposed_fix=raw.get("proposed_fix"),
        matching_fix_ref=raw.get("matching_fix_ref"),
    )
    return apply_already_fixed_gate(verdict, report, enrichment)
