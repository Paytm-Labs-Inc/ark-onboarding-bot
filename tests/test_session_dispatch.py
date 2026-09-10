"""Tests for Case 2 fix plan generation and dispatch."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.scout import ScoutReport
from src.session_classifier import DebugVerdict
from src.session_dispatch import FixPlan, dispatch_fix, generate_fix_plan
from src.session_enrichers import EnrichmentBundle


class FixPlanTests(unittest.TestCase):
    @patch("src.session_dispatch.completion_json")
    def test_generate_fix_plan(self, mock_json: MagicMock) -> None:
        mock_json.return_value = {
            "summary": "Raise action timeout",
            "root_cause": "verify action timed out",
            "proposed_fix": "Increase timeout in workspace actions",
            "target_files": ["workspaces/foo.yaml"],
            "test_plan": "ark workspace action_run ...",
        }
        report = ScoutReport(session_id="s-abc1234567", found=True, error="timeout")
        verdict = DebugVerdict(
            case="needs_fix",
            confidence=0.8,
            summary="timeout",
            root_cause="timeout",
            evidence=[],
        )
        plan = generate_fix_plan(report, EnrichmentBundle(), verdict)
        self.assertIn("timed out", plan.root_cause)
        self.assertIn("workspaces/foo.yaml", plan.target_files)
        self.assertIn("Raise action timeout", plan.display_text())

    def test_fix_plan_display_text(self) -> None:
        plan = FixPlan(
            summary="Fix null deref",
            root_cause="missing guard",
            proposed_fix="Add early return when token is None",
            target_files=["src/auth.py"],
            test_plan="python -m unittest tests.test_auth",
        )
        text = plan.display_text()
        self.assertIn("src/auth.py", text)
        self.assertIn("python -m unittest", text)


if __name__ == "__main__":
    unittest.main()
