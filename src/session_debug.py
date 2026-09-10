"""Top-level session debug orchestrator for Ask Ark."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from src.ark_client import ArkError
from src.codegraph_client import is_foundry_codebase, project_path
from src.scout import ScoutReport, gather_scout_report
from src.session_classifier import DebugVerdict, classify_session
from src.session_dispatch import (
    create_pending_plan,
    dispatch_fix,
    generate_fix_plan,
    get_pending,
    poll_and_create_pr,
    reject_pending,
)
from src.session_actions import (
    action_menu,
    create_action_gate,
    remaining_actions,
    run_action,
)
from src.session_enrichers import EnrichmentBundle, enrich_report
from src.session_handlers import (
    handle_already_fixed,
    handle_cannot_fix,
    handle_needs_fix,
)
from src.session_timeline import build_timeline, prepend_timeline, timeline_to_dict


@dataclass
class DebugResult:
    answer: str
    case: str | None = None
    ark_session_id: str | None = None
    gate_id: str | None = None
    gate_pending: bool = False
    gate_kind: str | None = None
    gate_actions: list[dict[str, str]] = field(default_factory=list)
    dispatch_session_id: str | None = None
    pr_url: str | None = None
    verdict: dict[str, Any] | None = None
    fix_plan: dict[str, Any] | None = None
    scout: dict[str, Any] | None = None
    timeline: dict[str, Any] | None = None
    debug: bool = True
    citations: list[str] = field(default_factory=list)
    retrieved_sources: list[str] = field(default_factory=list)

    def to_ask_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": self.citations,
            "retrieved_sources": self.retrieved_sources,
            "debug": True,
            "case": self.case,
            "ark_session_id": self.ark_session_id,
            "gate_id": self.gate_id,
            "gate_pending": self.gate_pending,
            "gate_kind": self.gate_kind,
            "gate_actions": self.gate_actions,
            "dispatch_session_id": self.dispatch_session_id,
            "pr_url": self.pr_url,
            "verdict": self.verdict,
            "fix_plan": self.fix_plan,
            "scout": self.scout,
            "timeline": self.timeline,
        }


def _scout_dict(report: ScoutReport) -> dict[str, Any]:
    return {
        "session_id": report.session_id,
        "found": report.found,
        "status": report.status,
        "stage": report.stage,
        "error": report.error,
        "flow_name": report.flow_name,
        "workspace_name": report.workspace_name,
    }


def _codegraph_block(enrichment: EnrichmentBundle | None) -> str:
    if not enrichment or not enrichment.codegraph_summary.strip():
        return ""
    root = project_path()
    label = "Foundry codebase (CodeGraph)" if is_foundry_codebase(root) else "Codebase (CodeGraph)"
    return f"{label}:\n{enrichment.codegraph_summary.strip()[:2500]}"


def _finish_debug_result(
    report: ScoutReport,
    *,
    answer: str,
    case: str,
    ark_session_id: str,
    verdict: DebugVerdict | None = None,
    sources: list[str] | None = None,
    gate_id: str | None = None,
    gate_pending: bool = False,
    gate_kind: str | None = None,
    gate_actions: list[dict[str, str]] | None = None,
    fix_plan: dict[str, Any] | None = None,
    enrichment: EnrichmentBundle | None = None,
) -> DebugResult:
    body = answer
    codegraph = _codegraph_block(enrichment)
    if codegraph:
        body = f"{codegraph}\n\n{body}"
    timeline = build_timeline(report)
    return DebugResult(
        answer=prepend_timeline(report, body),
        case=case,
        ark_session_id=ark_session_id,
        gate_id=gate_id,
        gate_pending=gate_pending,
        gate_kind=gate_kind,
        gate_actions=gate_actions or [],
        verdict=verdict.to_dict() if verdict else None,
        fix_plan=fix_plan,
        scout=_scout_dict(report),
        timeline=timeline_to_dict(timeline),
        citations=(sources or [])[:5],
        retrieved_sources=sources or [],
    )


def debug_session(ark_session_id: str) -> DebugResult:
    """Run the full session debug pipeline for one Ark session id."""
    report = gather_scout_report(ark_session_id)
    if not report.found:
        return DebugResult(
            answer=f"Could not debug session `{ark_session_id}`: {report.error}",
            case="cannot_fix",
            ark_session_id=ark_session_id,
            scout=_scout_dict(report),
        )

    enrichment = enrich_report(report)
    verdict = classify_session(report, enrichment)
    sources = [str(c.get("source", "")) for c in enrichment.rag_chunks if c.get("source")]

    if verdict.case == "already_fixed":
        action_gate = create_action_gate(
            report, verdict, enrichment, case="already_fixed"
        )
        menu = action_menu("already_fixed", verdict)
        return _finish_debug_result(
            report,
            answer=handle_already_fixed(report, verdict, enrichment),
            case="already_fixed",
            ark_session_id=ark_session_id,
            verdict=verdict,
            sources=sources,
            gate_id=action_gate.gate_id,
            gate_pending=bool(menu),
            gate_kind="next_steps",
            gate_actions=menu,
            enrichment=enrichment,
        )

    if verdict.case == "needs_fix":
        plan = generate_fix_plan(report, enrichment, verdict)
        pending = create_pending_plan(report, enrichment, verdict, plan)
        create_action_gate(
            report,
            verdict,
            enrichment,
            case="needs_fix",
            gate_id=pending.plan_id,
            fix_plan=plan,
        )
        menu = action_menu("needs_fix", verdict)
        return _finish_debug_result(
            report,
            answer=handle_needs_fix(report, verdict, plan, pending),
            case="needs_fix",
            ark_session_id=ark_session_id,
            verdict=verdict,
            sources=sources,
            gate_id=pending.plan_id,
            gate_pending=True,
            gate_kind="fix_plan",
            gate_actions=menu,
            fix_plan=plan.to_dict(),
            enrichment=enrichment,
        )

    action_gate = create_action_gate(report, verdict, enrichment, case="cannot_fix")
    menu = action_menu("cannot_fix", verdict)
    return _finish_debug_result(
        report,
        answer=handle_cannot_fix(report, verdict),
        case="cannot_fix",
        ark_session_id=ark_session_id,
        verdict=verdict,
        sources=sources,
        gate_id=action_gate.gate_id,
        gate_pending=bool(menu),
        gate_kind="next_steps",
        gate_actions=menu,
        enrichment=enrichment,
    )


def approve_plan(plan_id: str) -> DebugResult:
    """Run dispatch after the user approves a Case 2 fix plan."""
    pending = get_pending(plan_id)
    if not pending:
        return DebugResult(
            answer=(
                f"No pending fix plan found for id {plan_id}. "
                "It may have expired or already been handled."
            ),
            case="needs_fix",
            gate_id=plan_id,
        )

    try:
        dispatch = dispatch_fix(pending)
    except ArkError as exc:
        return DebugResult(
            answer=f"Dispatch failed: {exc}",
            case="needs_fix",
            gate_id=plan_id,
            ark_session_id=pending.ark_session_id,
            fix_plan=pending.plan.to_dict(),
        )

    dispatch_id = str(dispatch.get("dispatch_session_id") or "")
    pr_info = poll_and_create_pr(dispatch_id)
    pr_url = pr_info.get("pr_url")

    lines = [
        "Fix plan approved — Ark fix session started.",
        f"Fix session: {dispatch_id or '(unknown)'}",
        f"Original failed session: {pending.ark_session_id}",
    ]
    if pr_url:
        lines.append(f"PR: {pr_url}")
    else:
        lines.append(
            str(pr_info.get("message") or "PR not ready yet — check Ark when the session completes.")
        )

    return DebugResult(
        answer="\n".join(lines),
        case="needs_fix",
        ark_session_id=pending.ark_session_id,
        dispatch_session_id=dispatch_id or None,
        pr_url=str(pr_url) if pr_url else None,
        verdict=pending.verdict.to_dict(),
        fix_plan=pending.plan.to_dict(),
    )


def reject_plan(plan_id: str) -> DebugResult:
    pending = get_pending(plan_id)
    if pending:
        reject_pending(plan_id)
        return DebugResult(
            answer=(
                f"Fix plan rejected for session {pending.ark_session_id}. "
                "No fix session was started."
            ),
            case="needs_fix",
            gate_id=plan_id,
            ark_session_id=pending.ark_session_id,
            fix_plan=pending.plan.to_dict(),
        )
    return DebugResult(
        answer=f"No pending fix plan found for id {plan_id}.",
        gate_id=plan_id,
    )


# Backward-compatible aliases for existing API routes.
approve_dispatch = approve_plan
reject_dispatch = reject_plan


def debug_session_stream(ark_session_id: str) -> Iterator[dict[str, Any]]:
    """Yield progress events then a final done payload (for SSE)."""
    yield {"type": "progress", "step": "scout", "message": "Gathering session data…"}
    report = gather_scout_report(ark_session_id)
    if not report.found:
        yield {
            "type": "done",
            **DebugResult(
                answer=f"Could not debug session `{ark_session_id}`: {report.error}",
                case="cannot_fix",
                ark_session_id=ark_session_id,
            ).to_ask_dict(),
        }
        return

    yield {"type": "progress", "step": "timeline", "message": "Building diagnosis timeline…"}

    yield {"type": "progress", "step": "enrich", "message": "Searching docs and codebase…"}
    enrichment = enrich_report(report)
    sources = [str(c.get("source", "")) for c in enrichment.rag_chunks if c.get("source")]

    yield {"type": "progress", "step": "classify", "message": "Classifying issue…"}
    verdict = classify_session(report, enrichment)

    if verdict.case == "needs_fix":
        yield {"type": "progress", "step": "plan", "message": "Drafting fix plan…"}
        plan = generate_fix_plan(report, enrichment, verdict)
        pending = create_pending_plan(report, enrichment, verdict, plan)
        create_action_gate(
            report,
            verdict,
            enrichment,
            case="needs_fix",
            gate_id=pending.plan_id,
            fix_plan=plan,
        )
        menu = action_menu("needs_fix", verdict)
        result = _finish_debug_result(
            report,
            answer=handle_needs_fix(report, verdict, plan, pending),
            case="needs_fix",
            ark_session_id=ark_session_id,
            verdict=verdict,
            sources=sources,
            gate_id=pending.plan_id,
            gate_pending=True,
            gate_kind="fix_plan",
            gate_actions=menu,
            fix_plan=plan.to_dict(),
            enrichment=enrichment,
        )
    elif verdict.case == "already_fixed":
        action_gate = create_action_gate(
            report, verdict, enrichment, case="already_fixed"
        )
        menu = action_menu("already_fixed", verdict)
        result = _finish_debug_result(
            report,
            answer=handle_already_fixed(report, verdict, enrichment),
            case="already_fixed",
            ark_session_id=ark_session_id,
            verdict=verdict,
            sources=sources,
            gate_id=action_gate.gate_id,
            gate_pending=bool(menu),
            gate_kind="next_steps",
            gate_actions=menu,
            enrichment=enrichment,
        )
    else:
        action_gate = create_action_gate(report, verdict, enrichment, case="cannot_fix")
        menu = action_menu("cannot_fix", verdict)
        result = _finish_debug_result(
            report,
            answer=handle_cannot_fix(report, verdict),
            case="cannot_fix",
            ark_session_id=ark_session_id,
            verdict=verdict,
            sources=sources,
            gate_id=action_gate.gate_id,
            gate_pending=bool(menu),
            gate_kind="next_steps",
            gate_actions=menu,
            enrichment=enrichment,
        )

    yield {"type": "done", **result.to_ask_dict()}


def run_debug_action(gate_id: str, action: str) -> DebugResult:
    """Generate a next-step artifact (Slack message, ticket, follow-up prompt)."""
    answer, pending = run_action(gate_id, action)
    if pending is None:
        return DebugResult(answer=answer, gate_id=gate_id)

    left = remaining_actions(pending)
    plan_still_pending = pending.case == "needs_fix" and get_pending(gate_id) is not None
    has_helpers = bool(left)
    if plan_still_pending:
        gate_kind: str | None = "fix_plan"
    elif has_helpers:
        gate_kind = "next_steps"
    else:
        gate_kind = None

    return DebugResult(
        answer=answer,
        case=pending.case,
        ark_session_id=pending.ark_session_id,
        gate_id=gate_id,
        gate_pending=plan_still_pending or has_helpers,
        gate_kind=gate_kind,
        gate_actions=left,
        verdict=pending.verdict.to_dict(),
        fix_plan=pending.fix_plan.to_dict() if pending.fix_plan else None,
    )
