"""Tests for session debug orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.session_classifier import DebugVerdict
from src.session_debug import debug_session
from src.session_dispatch import FixPlan
from src.scout import ScoutReport
from src.session_enrichers import EnrichmentBundle


class SessionDebugTests(unittest.TestCase):
    @patch("src.session_debug.classify_session")
    @patch("src.session_debug.enrich_report")
    @patch("src.session_debug.gather_scout_report")
    def test_debug_session_already_fixed(
        self,
        mock_gather: MagicMock,
        mock_enrich: MagicMock,
        mock_classify: MagicMock,
    ) -> None:
        mock_gather.return_value = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="timeout",
            flow_name="ark-feature",
        )
        mock_enrich.return_value = EnrichmentBundle(
            changelog_hits=[{"ref": "abc123", "subject": "fix timeout", "date": "2026-01-01"}]
        )
        mock_classify.return_value = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Fixed on main",
            root_cause="action timeout too low",
            evidence=["commit abc123"],
            matching_fix_ref="abc123",
        )
        result = debug_session("s-abc1234567")
        self.assertEqual(result.case, "already_fixed")
        self.assertIn("Session s-abc1234567", result.answer)
        self.assertIn("what failed", result.answer.lower())
        self.assertIn("still failed", result.answer.lower())

    @patch("src.session_debug.generate_fix_plan")
    @patch("src.session_debug.classify_session")
    @patch("src.session_debug.enrich_report")
    @patch("src.session_debug.gather_scout_report")
    def test_debug_session_needs_fix_shows_plan_only(
        self,
        mock_gather: MagicMock,
        mock_enrich: MagicMock,
        mock_classify: MagicMock,
        mock_plan: MagicMock,
    ) -> None:
        report = ScoutReport(session_id="s-abc1234567", found=True, error="bug")
        mock_gather.return_value = report
        mock_enrich.return_value = EnrichmentBundle()
        mock_classify.return_value = DebugVerdict(
            case="needs_fix",
            confidence=0.8,
            summary="Needs a code fix",
            root_cause="null pointer",
            evidence=["stack trace"],
            proposed_fix="Add null check",
        )
        mock_plan.return_value = FixPlan(
            summary="Add null check in handler",
            root_cause="null pointer",
            proposed_fix="Guard the handler entry with if value is None: return",
            target_files=["src/handler.py"],
            test_plan="python -m unittest tests.test_handler",
        )
        result = debug_session("s-abc1234567")
        self.assertEqual(result.case, "needs_fix")
        self.assertTrue(result.gate_pending)
        self.assertIsNotNone(result.gate_id)
        self.assertIn("Suggested fix plan", result.answer)
        self.assertNotIn("Approve plan", result.answer)
        self.assertNotIn("Reject plan", result.answer)
        self.assertIn("src/handler.py", result.answer)
        self.assertIsNotNone(result.fix_plan)
        self.assertEqual(result.gate_kind, "next_steps")


if __name__ == "__main__":
    unittest.main()
