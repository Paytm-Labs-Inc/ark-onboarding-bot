"""Fix plan generation for Case 2 (needs_fix)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.llm_json import completion_json
from src.scout import ScoutReport
from src.session_classifier import DebugVerdict
from src.session_enrichers import EnrichmentBundle

_PLANNER_PROMPT = """You are the fix planner for an Ark session failure.

Given Scout data and enrichments, write a concrete fix plan a developer can review.

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


def generate_fix_plan(
    report: ScoutReport,
    enrichment: EnrichmentBundle,
    verdict: DebugVerdict,
) -> FixPlan:
    """Draft a fix plan for the user to review — no dispatch or repo changes."""
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
