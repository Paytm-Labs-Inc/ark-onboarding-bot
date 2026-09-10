"""Fix plan generation and Ark dispatch for Case 2 (needs_fix)."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.ark_client import ArkClient, ArkError, default_client
from src.llm_json import completion_json
from src.scout import ScoutReport
from src.session_classifier import DebugVerdict
from src.session_enrichers import EnrichmentBundle

_PLANNER_PROMPT = """You are the fix planner for an Ark session failure.

Given Scout data and enrichments, write a concrete fix plan a developer can review before any code runs.

Respond JSON only:
{{
  "summary": "one sentence for the user",
  "root_cause": "technical root cause",
  "proposed_fix": "step-by-step what to change and why",
  "target_files": ["path/or/file"],
  "test_plan": "how to verify the fix"
}}

Keep scope bounded (roughly <=10 files). If the failure is infra/platform, say so in root_cause.

## Scout
{scout}

## Context
{context}

## Initial verdict
{verdict}
"""


@dataclass
class FixPlan:
    summary: str
    root_cause: str
    proposed_fix: str
    target_files: list[str] = field(default_factory=list)
    test_plan: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "root_cause": self.root_cause,
            "proposed_fix": self.proposed_fix,
            "target_files": self.target_files,
            "test_plan": self.test_plan,
        }

    def display_text(self) -> str:
        lines = [
            self.summary,
            f"Root cause: {self.root_cause}",
            "",
            "Proposed changes:",
            self.proposed_fix,
        ]
        if self.target_files:
            lines.append("")
            lines.append("Files to touch:")
            lines.extend(f"  • {path}" for path in self.target_files[:12])
        if self.test_plan:
            lines.append("")
            lines.append("How to test:")
            lines.append(self.test_plan)
        return "\n".join(lines)


@dataclass
class PendingFixPlan:
    plan_id: str
    ark_session_id: str
    plan: FixPlan
    verdict: DebugVerdict
    report: ScoutReport
    enrichment: EnrichmentBundle
    created_at: float = field(default_factory=time.time)


_pending: dict[str, PendingFixPlan] = {}


def generate_fix_plan(
    report: ScoutReport,
    enrichment: EnrichmentBundle,
    verdict: DebugVerdict,
) -> FixPlan:
    """Draft a fix plan for human review — no auto-dispatch."""
    context = enrichment.rag_text()[:3000] + "\n" + enrichment.code_text()[:3000]
    try:
        plan_raw = completion_json(
            _PLANNER_PROMPT.format(
                scout=report.to_context_blob()[:8000],
                context=context,
                verdict=json.dumps(verdict.to_dict()),
            )
        )
    except Exception as exc:  # noqa: BLE001 — planner failure must not hide diagnosis
        return FixPlan(
            summary=str(verdict.summary or "Fix plan"),
            root_cause=str(verdict.root_cause or report.error or "unknown"),
            proposed_fix=str(
                verdict.proposed_fix
                or f"Review the failure manually. (Planner unavailable: {exc})"
            ),
            target_files=[],
            test_plan="Re-run the failing verify action after the fix.",
        )
    target_files = plan_raw.get("target_files") or []
    if not isinstance(target_files, list):
        target_files = [str(target_files)]
    return FixPlan(
        summary=str(plan_raw.get("summary") or verdict.summary or "Fix plan"),
        root_cause=str(plan_raw.get("root_cause") or verdict.root_cause or report.error or ""),
        proposed_fix=str(plan_raw.get("proposed_fix") or verdict.proposed_fix or ""),
        target_files=[str(f) for f in target_files[:12]],
        test_plan=str(plan_raw.get("test_plan") or ""),
    )


def create_pending_plan(
    report: ScoutReport,
    enrichment: EnrichmentBundle,
    verdict: DebugVerdict,
    plan: FixPlan,
) -> PendingFixPlan:
    plan_id = uuid.uuid4().hex[:12]
    pending = PendingFixPlan(
        plan_id=plan_id,
        ark_session_id=report.session_id,
        plan=plan,
        verdict=verdict,
        report=report,
        enrichment=enrichment,
    )
    _pending[plan_id] = pending
    return pending


def get_pending(plan_id: str) -> PendingFixPlan | None:
    return _pending.get(plan_id)


def reject_pending(plan_id: str) -> bool:
    return _pending.pop(plan_id, None) is not None


def _dispatch_defaults() -> dict[str, str]:
    return {
        "flow": os.environ.get("ARK_FIX_FLOW", "ark-feature").strip(),
        "workspace": os.environ.get("ARK_DEFAULT_WORKSPACE", "").strip(),
        "compute": os.environ.get("ARK_DEFAULT_COMPUTE", "").strip(),
    }


def dispatch_fix(
    pending: PendingFixPlan,
    *,
    client: ArkClient | None = None,
) -> dict[str, Any]:
    """Start an Ark fix session after the user approves the plan."""
    client = client or default_client()
    defaults = _dispatch_defaults()
    if not defaults["workspace"] or not defaults["compute"]:
        raise ArkError(
            "Set ARK_DEFAULT_WORKSPACE and ARK_DEFAULT_COMPUTE before dispatching fixes."
        )

    prompt = (
        f"Fix the failure in Ark session {pending.ark_session_id}.\n\n"
        f"Root cause: {pending.plan.root_cause}\n\n"
        f"Approved plan:\n{pending.plan.display_text()}\n\n"
        f"Scout context:\n{pending.report.to_context_blob()[:6000]}"
    )

    result = client.session_lifecycle(
        "start",
        flow=defaults["flow"],
        workspace=defaults["workspace"],
        compute=defaults["compute"],
        autonomy="full",
        summary=f"Ask Ark fix for {pending.ark_session_id}: {pending.plan.summary[:120]}",
        prompt=prompt,
    )

    dispatch_id = ""
    if isinstance(result, dict):
        dispatch_id = str(
            result.get("sessionId")
            or result.get("session_id")
            or result.get("id")
            or ""
        )
        nested = result.get("session")
        if not dispatch_id and isinstance(nested, dict):
            dispatch_id = str(nested.get("id") or "")

    _pending.pop(pending.plan_id, None)
    return {"dispatch_session_id": dispatch_id, "raw": result}


def poll_and_create_pr(
    dispatch_session_id: str,
    *,
    client: ArkClient | None = None,
    max_wait_seconds: int = 30,
) -> dict[str, Any]:
    """Best-effort: check dispatch session and open PR if terminal."""
    del max_wait_seconds  # reserved for a future poll loop
    client = client or default_client()
    if not dispatch_session_id:
        return {"pr_url": None, "message": "No dispatch session id returned."}

    show = client.session_read("show", sessionId=dispatch_session_id)
    show_dict = show
    if isinstance(show, dict) and isinstance(show.get("session"), dict):
        show_dict = show["session"]
    status = str((show_dict or {}).get("status") or "").lower() if isinstance(show_dict, dict) else ""
    if status not in ("completed", "failed", "stopped", "archived"):
        return {
            "pr_url": None,
            "message": (
                f"Fix session {dispatch_session_id} is still {status or 'running'}. "
                "Check Ark when it completes."
            ),
            "dispatch_session_id": dispatch_session_id,
        }

    pr_result = client.worktree(
        "create_pr",
        sessionId=dispatch_session_id,
        title=f"Fix for {dispatch_session_id}",
    )
    pr_url = None
    if isinstance(pr_result, dict):
        pr_url = pr_result.get("pr_url") or pr_result.get("compareUrl")
    return {
        "pr_url": pr_url,
        "dispatch_session_id": dispatch_session_id,
        "raw": pr_result,
    }
