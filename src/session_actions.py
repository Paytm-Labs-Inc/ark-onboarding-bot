"""In-chat next-step actions for all session-debug cases."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from src.argocd_client import format_deployment_status
from src.llm_json import completion_json
from src.scout import ScoutReport
from src.session_classifier import DebugVerdict
from src.session_dispatch import FixPlan
from src.session_enrichers import EnrichmentBundle

DebugCaseName = Literal["already_fixed", "needs_fix", "cannot_fix"]

# Each action lists which cases it applies to. For cannot_fix, ``reasons`` further
# filters by verdict.cannot_fix_reason — Case 3 is not always the same three buttons.
ACTION_SPECS: dict[str, dict[str, Any]] = {
    # --- Case 1: already fixed on main ---
    "check_deployment": {
        "label": "Check deployment status",
        "prompt_key": None,
        "cases": ["already_fixed"],
    },
    "rerun_instructions": {
        "label": "Generate re-run instructions",
        "prompt_key": "rerun",
        "cases": ["already_fixed"],
    },
    "explain_fix": {
        "label": "Explain the fix in plain English",
        "prompt_key": "explain_fix",
        "cases": ["already_fixed"],
    },
    "slack_message": {
        "label": "Generate #foundry-users message",
        "prompt_key": "slack",
        "cases": ["already_fixed", "needs_fix", "cannot_fix"],
    },
    # --- Case 2: needs a code fix ---
    "simplify_plan": {
        "label": "Explain fix plan simply",
        "prompt_key": "simplify_plan",
        "cases": ["needs_fix"],
    },
    "test_checklist": {
        "label": "Generate test checklist",
        "prompt_key": "test_checklist",
        "cases": ["needs_fix"],
    },
    # --- Case 3: cannot auto-fix (reason-specific) ---
    "infra_ticket": {
        "label": "Generate infra ticket",
        "prompt_key": "ticket",
        "cases": ["cannot_fix"],
        "reasons": ["infra", "unknown"],
    },
    "credentials_guide": {
        "label": "Generate credentials checklist",
        "prompt_key": "credentials",
        "cases": ["cannot_fix"],
        "reasons": ["permissions"],
    },
    "feature_request": {
        "label": "Generate feature request ticket",
        "prompt_key": "feature_request",
        "cases": ["cannot_fix"],
        "reasons": ["new_feature"],
    },
    "scope_breakdown": {
        "label": "Break task into smaller pieces",
        "prompt_key": "scope_breakdown",
        "cases": ["cannot_fix"],
        "reasons": ["too_large"],
    },
    "follow_up_prompt": {
        "label": "Draft smaller follow-up session",
        "prompt_key": "follow_up",
        "cases": ["cannot_fix"],
        "reasons": ["too_large", "new_feature", "unknown", "infra"],
    },
}

_ACTION_PROMPTS = {
    "slack": """Write a Slack message for #foundry-users about a failed Ark session.
Include session id, stage, error, summary, and what help is needed. Keep it concise and copy-paste ready.
Respond JSON only: {"title": "short subject line", "body": "full message text"}""",
    "rerun": """The fix may already be on main but this session still failed.
Write step-by-step instructions to verify deployment and re-run the same Ark session safely.
Respond JSON only: {"title": "short heading", "body": "numbered steps"}""",
    "explain_fix": """Explain the changelog-matched fix in plain English for a developer who hit this error.
Respond JSON only: {"title": "short heading", "body": "plain-English explanation"}""",
    "simplify_plan": """Explain the proposed code fix plan in simple, non-jargony language for a reviewer.
Respond JSON only: {"title": "short heading", "body": "simple explanation"}""",
    "test_checklist": """Create a verification checklist after the proposed fix is merged.
Respond JSON only: {"title": "short heading", "body": "checklist items"}""",
    "ticket": """Draft an infra/platform ticket for a failed Ark session.
Include title, impact, reproduction steps, session id, error, and suggested owner team.
Respond JSON only: {"title": "ticket title", "body": "full ticket description"}""",
    "credentials": """Draft a credentials/permissions troubleshooting guide for this Ark session failure.
List secrets to check, scopes needed, and where to set them in Foundry.
Respond JSON only: {"title": "short heading", "body": "checklist and steps"}""",
    "feature_request": """Draft a feature request ticket — this is out of scope for auto-fix.
Include user goal, gap, session id, and suggested product area.
Respond JSON only: {"title": "ticket title", "body": "full description"}""",
    "scope_breakdown": """The task was too large for one session. Break it into 3–5 smaller Ark sessions the user can run sequentially.
Respond JSON only: {"title": "short heading", "body": "numbered sub-tasks with dispatch prompts"}""",
    "follow_up": """Draft a smaller follow-up session prompt the user can paste into Ark to retry with reduced scope.
Respond JSON only: {"title": "one-line goal", "body": "full dispatch prompt with narrowed scope"}""",
}


@dataclass
class PendingDebugActions:
    gate_id: str
    case: DebugCaseName
    ark_session_id: str
    report: ScoutReport
    verdict: DebugVerdict
    enrichment: EnrichmentBundle
    fix_plan: FixPlan | None = None
    completed: set[str] = field(default_factory=set)


_pending_actions: dict[str, PendingDebugActions] = {}


def _action_ids_for(case: DebugCaseName, verdict: DebugVerdict) -> list[str]:
    reason = verdict.cannot_fix_reason or "unknown"
    ids: list[str] = []
    for action_id, spec in ACTION_SPECS.items():
        if case not in spec.get("cases", []):
            continue
        if case == "cannot_fix":
            allowed = spec.get("reasons")
            if allowed is not None and reason not in allowed:
                continue
        ids.append(action_id)
    return ids


def action_menu(case: DebugCaseName, verdict: DebugVerdict) -> list[dict[str, str]]:
    return [
        {"id": action_id, "label": ACTION_SPECS[action_id]["label"]}
        for action_id in _action_ids_for(case, verdict)
    ]


def action_marker(action_id: str, label: str) -> str:
    """Inline link marker parsed by the chat UI: [[action:id|label]]."""
    return f"[[action:{action_id}|{label}]]"


def _join_action_markers(markers: list[str]) -> str:
    if len(markers) == 1:
        return markers[0]
    if len(markers) == 2:
        return f"{markers[0]} or {markers[1]}"
    return ", ".join(markers[:-1]) + f", or {markers[-1]}"


def action_intro_lines(case: DebugCaseName, menu: list[dict[str, str]]) -> list[str]:
    if not menu:
        return []
    links = _join_action_markers(
        [action_marker(item["id"], item["label"]) for item in menu]
    )
    if case == "needs_fix":
        return ["", f"While you review, I can also {links}."]
    return ["", f"I can help you finish next steps right here — {links}."]


def create_action_gate(
    report: ScoutReport,
    verdict: DebugVerdict,
    enrichment: EnrichmentBundle,
    *,
    case: DebugCaseName,
    gate_id: str | None = None,
    fix_plan: FixPlan | None = None,
) -> PendingDebugActions:
    gate_id = gate_id or uuid.uuid4().hex[:12]
    pending = PendingDebugActions(
        gate_id=gate_id,
        case=case,
        ark_session_id=report.session_id,
        report=report,
        verdict=verdict,
        enrichment=enrichment,
        fix_plan=fix_plan,
    )
    _pending_actions[gate_id] = pending
    return pending


def get_action_gate(gate_id: str) -> PendingDebugActions | None:
    return _pending_actions.get(gate_id)


def _context_blob(pending: PendingDebugActions) -> str:
    parts = [
        pending.report.to_context_blob()[:6000],
        f"Case: {pending.case}",
        f"Summary: {pending.verdict.summary or ''}",
        f"Root cause: {pending.verdict.root_cause or ''}",
        pending.enrichment.rag_text()[:1500],
        pending.enrichment.code_text()[:1500],
    ]
    if pending.fix_plan:
        parts.append(f"Fix plan:\n{pending.fix_plan.display_text()[:2000]}")
    if pending.verdict.matching_fix_ref:
        parts.append(f"Matching fix ref: {pending.verdict.matching_fix_ref}")
    return "\n\n".join(p for p in parts if p.strip())


def _format_generated(action: str, raw: dict[str, Any]) -> str:
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not body:
        body = str(raw.get("message") or raw.get("text") or "").strip()
    label = ACTION_SPECS.get(action, {}).get("label", action)
    lines = [f"**{label}**"]
    if title:
        lines.append(f"Title: {title}")
    lines.append("")
    lines.append(body or "(no content generated)")
    lines.append("")
    lines.append("Copy the text above where you need it.")
    return "\n".join(lines)


def _run_check_deployment(pending: PendingDebugActions) -> str:
    ref = pending.verdict.matching_fix_ref
    status = format_deployment_status(ref).strip()
    label = ACTION_SPECS["check_deployment"]["label"]
    if status:
        return f"**{label}**\n\n{status}"
    return (
        f"**{label}**\n\n"
        "ArgoCD is not configured (set ARGOCD_URL, ARGOCD_TOKEN, ARGOCD_APPS) "
        "or returned no data. Check the control-plane deployment manually, then re-run the session."
    )


def run_action(gate_id: str, action: str) -> tuple[str, PendingDebugActions | None]:
    """Generate one next-step artifact. Returns (answer_text, pending_state)."""
    pending = get_action_gate(gate_id)
    if not pending:
        return f"No pending debug actions found for id {gate_id}.", None

    allowed = set(_action_ids_for(pending.case, pending.verdict))
    if action not in allowed:
        return f"Unknown or unavailable action: {action}", pending

    if action == "check_deployment":
        answer = _run_check_deployment(pending)
    else:
        prompt_key = ACTION_SPECS[action].get("prompt_key")
        if not prompt_key:
            return f"Action {action} is not implemented.", pending
        prompt = (
            _ACTION_PROMPTS[prompt_key]
            + "\n\n## Session context\n"
            + _context_blob(pending)
        )
        try:
            raw = completion_json(prompt)
            answer = _format_generated(action, raw)
        except Exception as exc:  # noqa: BLE001 — show failure in chat
            return (
                f"Could not generate {ACTION_SPECS[action]['label']}: {exc}",
                pending,
            )

    pending.completed.add(action)
    return answer, pending


def remaining_actions(pending: PendingDebugActions) -> list[dict[str, str]]:
    menu = action_menu(pending.case, pending.verdict)
    return [item for item in menu if item["id"] not in pending.completed]
