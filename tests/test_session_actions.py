"""Tests for in-chat next-step actions across all debug cases."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.scout import ScoutReport
from src.session_actions import (
    action_intro_lines,
    action_menu,
    create_action_gate,
    run_action,
)
from src.session_classifier import DebugVerdict
from src.session_debug import debug_session, run_debug_action
from src.session_enrichers import EnrichmentBundle


class SessionActionsTests(unittest.TestCase):
    def test_already_fixed_menu(self) -> None:
        verdict = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Fixed",
            root_cause="timeout",
            evidence=[],
            matching_fix_ref="abc123",
        )
        ids = {item["id"] for item in action_menu("already_fixed", verdict)}
        self.assertIn("check_deployment", ids)
        self.assertIn("slack_message", ids)

    def test_needs_fix_menu(self) -> None:
        verdict = DebugVerdict(
            case="needs_fix",
            confidence=0.8,
            summary="Bug",
            root_cause="null",
            evidence=[],
        )
        ids = {item["id"] for item in action_menu("needs_fix", verdict)}
        self.assertIn("simplify_plan", ids)
        self.assertIn("test_checklist", ids)

    def test_cannot_fix_infra_menu(self) -> None:
        verdict = DebugVerdict(
            case="cannot_fix",
            confidence=0.9,
            summary="Platform",
            root_cause="arkd",
            evidence=[],
            cannot_fix_reason="infra",
        )
        ids = {item["id"] for item in action_menu("cannot_fix", verdict)}
        self.assertIn("infra_ticket", ids)
        self.assertIn("slack_message", ids)
        self.assertNotIn("credentials_guide", ids)

    def test_cannot_fix_permissions_menu_differs(self) -> None:
        verdict = DebugVerdict(
            case="cannot_fix",
            confidence=0.9,
            summary="Secrets",
            root_cause="missing token",
            evidence=[],
            cannot_fix_reason="permissions",
        )
        ids = {item["id"] for item in action_menu("cannot_fix", verdict)}
        self.assertIn("credentials_guide", ids)
        self.assertNotIn("infra_ticket", ids)

    def test_action_intro_lines_use_inline_markers(self) -> None:
        menu = [
            {"id": "slack_message", "label": "Generate #foundry-users message"},
            {"id": "infra_ticket", "label": "Generate infra ticket"},
            {"id": "follow_up_prompt", "label": "Draft smaller follow-up session"},
        ]
        text = "\n".join(action_intro_lines("cannot_fix", menu))
        self.assertIn("[[action:slack_message|Generate #foundry-users message]]", text)
        self.assertIn("[[action:infra_ticket|Generate infra ticket]]", text)
        self.assertIn("[[action:follow_up_prompt|Draft smaller follow-up session]]", text)
        self.assertNotIn("Click a button below", text)

    def test_cannot_fix_too_large_menu(self) -> None:
        verdict = DebugVerdict(
            case="cannot_fix",
            confidence=0.9,
            summary="Huge",
            root_cause="scope",
            evidence=[],
            cannot_fix_reason="too_large",
        )
        ids = {item["id"] for item in action_menu("cannot_fix", verdict)}
        self.assertIn("scope_breakdown", ids)
        self.assertIn("follow_up_prompt", ids)
        self.assertNotIn("infra_ticket", ids)

    @patch("src.session_actions.completion_json")
    def test_run_action_generates_slack_message(self, mock_json: MagicMock) -> None:
        mock_json.return_value = {
            "title": "Session s-abc failed at triage",
            "body": "Hi team — session s-abc1234567 failed with arkd error.",
        }
        report = ScoutReport(session_id="s-abc1234567", found=True, error="arkd boom")
        verdict = DebugVerdict(
            case="cannot_fix",
            confidence=0.9,
            summary="Platform issue",
            root_cause="arkd",
            evidence=["infra"],
            cannot_fix_reason="infra",
        )
        pending = create_action_gate(
            report, verdict, EnrichmentBundle(), case="cannot_fix"
        )
        answer, state = run_action(pending.gate_id, "slack_message")
        self.assertIn("#foundry-users", answer)
        self.assertIn("s-abc1234567", answer)
        self.assertIsNotNone(state)
        self.assertIn("slack_message", state.completed)

    @patch("src.session_debug.classify_session")
    @patch("src.session_debug.enrich_report")
    @patch("src.session_debug.gather_scout_report")
    def test_cannot_fix_exposes_contextual_actions(
        self,
        mock_gather: MagicMock,
        mock_enrich: MagicMock,
        mock_classify: MagicMock,
    ) -> None:
        mock_gather.return_value = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="infra failure",
        )
        mock_enrich.return_value = EnrichmentBundle()
        mock_classify.return_value = DebugVerdict(
            case="cannot_fix",
            confidence=0.9,
            summary="Blocked",
            root_cause="compute",
            evidence=["not approved"],
            cannot_fix_reason="permissions",
        )
        result = debug_session("s-abc1234567")
        self.assertEqual(result.case, "cannot_fix")
        self.assertTrue(result.gate_pending)
        action_ids = {a["id"] for a in result.gate_actions}
        self.assertIn("credentials_guide", action_ids)
        self.assertNotIn("infra_ticket", action_ids)

    @patch("src.session_debug.classify_session")
    @patch("src.session_debug.enrich_report")
    @patch("src.session_debug.gather_scout_report")
    def test_already_fixed_exposes_action_gate(
        self,
        mock_gather: MagicMock,
        mock_enrich: MagicMock,
        mock_classify: MagicMock,
    ) -> None:
        mock_gather.return_value = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="timeout",
        )
        mock_enrich.return_value = EnrichmentBundle()
        mock_classify.return_value = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Fixed on main",
            root_cause="timeout",
            evidence=["commit abc"],
            matching_fix_ref="abc123",
        )
        result = debug_session("s-abc1234567")
        self.assertEqual(result.case, "already_fixed")
        self.assertTrue(result.gate_pending)
        self.assertIn("check_deployment", {a["id"] for a in result.gate_actions})

    @patch("src.session_debug.run_action")
    def test_run_debug_action_preserves_case(self, mock_run: MagicMock) -> None:
        from src.session_actions import PendingDebugActions

        pending = PendingDebugActions(
            gate_id="gate1",
            case="already_fixed",
            ark_session_id="s-abc1234567",
            report=ScoutReport(session_id="s-abc1234567", found=True),
            verdict=DebugVerdict(
                case="already_fixed",
                confidence=0.9,
                summary="x",
                root_cause="y",
                evidence=[],
            ),
            enrichment=EnrichmentBundle(),
            completed={"check_deployment"},
        )
        mock_run.return_value = ("Deployment status here.", pending)
        result = run_debug_action("gate1", "rerun_instructions")
        self.assertEqual(result.case, "already_fixed")
        self.assertTrue(result.gate_pending)


if __name__ == "__main__":
    unittest.main()
