"""Format chat responses for each session-debug case."""

from __future__ import annotations

from src.argocd_client import format_deployment_status
from src.scout import ScoutReport
from src.session_actions import action_intro_lines, action_menu
from src.session_classifier import DebugVerdict
from src.session_dispatch import FixPlan
from src.session_enrichers import EnrichmentBundle


def handle_already_fixed(
    report: ScoutReport,
    verdict: DebugVerdict,
    enrichment: EnrichmentBundle,
) -> str:
    stage = report.stage or "unknown"
    error = report.error or verdict.root_cause or "unknown error"
    fix_ref = verdict.matching_fix_ref or ""

    lines = [
        "What failed (this session):",
        f"  Stage: {stage}",
        f"  Error: {error}",
        "",
        "Possible fix on main (strong changelog match):",
    ]
    if verdict.evidence:
        for item in verdict.evidence[:4]:
            lines.append(f"  • {item}")
    elif fix_ref:
        lines.append(f"  • {fix_ref}")
    else:
        lines.append("  • (no match detail recorded)")

    lines.extend(
        [
            "",
            "This session still failed with the error above.",
            "Check whether the fix is merged and deployed, then re-run.",
        ]
    )

    deploy = format_deployment_status(fix_ref or None).strip()
    if deploy:
        lines.append("")
        lines.append(deploy)

    menu = action_menu("already_fixed", verdict)
    lines.extend(action_intro_lines("already_fixed", menu))
    return "\n".join(lines)


def handle_needs_fix(
    report: ScoutReport,
    verdict: DebugVerdict,
    plan: FixPlan,
) -> str:
    menu = action_menu("needs_fix", verdict)
    lines = [
        "This looks like a code bug that may need a fix.",
        "",
        "Suggested fix plan:",
        plan.display_text(),
    ]
    lines.extend(action_intro_lines("needs_fix", menu))
    return "\n".join(lines)


def handle_completed(report: ScoutReport) -> str:
    stage = report.stage or "unknown"
    lines = [
        "This session completed successfully — there is nothing to debug.",
        f"Final stage: {stage}",
    ]
    if report.session_summary:
        lines.append(f"Task: {report.session_summary}")
    if report.gather_errors:
        lines.extend(
            [
                "",
                "Note: some optional debug data could not be fetched "
                "(this does not change the session outcome):",
            ]
        )
        lines.extend(f"  • {err}" for err in report.gather_errors[:4])
    return "\n".join(lines)


def handle_cannot_fix(
    report: ScoutReport,
    verdict: DebugVerdict,
) -> str:
    reason = verdict.cannot_fix_reason or "unknown"
    reason_labels = {
        "new_feature": "This looks like a new feature request, not a bug fix.",
        "infra": "This is a platform issue — Ask Ark cannot auto-fix infrastructure problems.",
        "permissions": "This looks like a permissions or credentials issue.",
        "too_large": "The fix would touch too many files for automatic dispatch.",
        "unknown": "Ask Ark is not confident enough to propose an automatic fix.",
    }
    menu = action_menu("cannot_fix", verdict)
    lines = [
        reason_labels.get(reason, reason_labels["unknown"]),
        verdict.summary or "",
    ]
    lines.extend(action_intro_lines("cannot_fix", menu))
    if verdict.evidence:
        lines.append("")
        lines.append("Why:")
        lines.extend(f"  • {e}" for e in verdict.evidence)
    return "\n".join(line for line in lines if line).strip()


def format_scout_summary(report: ScoutReport) -> str:
    """Initial scout-only summary before full classification."""
    if not report.found:
        return f"Could not load session {report.session_id}: {report.error}"
    lines = [
        f"Session {report.session_id}",
        f"Status: {report.status or 'unknown'} · Stage: {report.stage or 'unknown'}",
    ]
    if report.flow_name:
        lines.append(f"Flow: {report.flow_name}")
    if report.workspace_name:
        lines.append(f"Workspace: {report.workspace_name}")
    if report.error:
        lines.append(f"Error: {report.error}")
    if report.cost_usd is not None:
        lines.append(f"Cost: ${report.cost_usd:.4f}")
    return "\n".join(lines)
