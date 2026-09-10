"""Tests for deterministic session classification heuristics."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.scout import ScoutReport
from src.session_classifier import classify_session
from src.session_enrichers import EnrichmentBundle


class SessionClassifierHeuristicTests(unittest.TestCase):
    def test_provisioning_timeout_is_cannot_fix_infra(self) -> None:
        report = ScoutReport(
            session_id="s-uararz0fay",
            found=True,
            status="failed",
            stage="smoke",
            error=(
                'provisioning did not complete within 20m (1200s elapsed across every attempt) '
                'for session s-uararz0fay on compute "ocl-cst-ticket-foundry-platform". '
                "The compute never became ready."
            ),
        )
        with patch("src.session_classifier.completion_json") as mock_llm:
            verdict = classify_session(report, EnrichmentBundle())
        mock_llm.assert_not_called()
        self.assertEqual(verdict.case, "cannot_fix")
        self.assertEqual(verdict.cannot_fix_reason, "infra")
        self.assertIsNone(verdict.proposed_fix)

    def test_arkd_bundle_failure_is_cannot_fix_infra(self) -> None:
        report = ScoutReport(
            session_id="s-deadbeef01",
            found=True,
            status="failed",
            stage="prepare",
            error="Cannot find module 'ark-darwin' from /$bunfs/root/arkd",
        )
        with patch("src.session_classifier.completion_json") as mock_llm:
            verdict = classify_session(report, EnrichmentBundle())
        mock_llm.assert_not_called()
        self.assertEqual(verdict.case, "cannot_fix")
        self.assertEqual(verdict.cannot_fix_reason, "infra")

    @patch("src.session_classifier.completion_json")
    def test_code_bug_still_uses_llm(self, mock_llm) -> None:
        mock_llm.return_value = {
            "case": "needs_fix",
            "confidence": 0.7,
            "summary": "Unit test failed",
            "root_cause": "AssertionError in test_foo",
            "evidence": ["verify stage red"],
            "cannot_fix_reason": None,
            "proposed_fix": "Fix the assertion",
        }
        report = ScoutReport(
            session_id="s-case2test01",
            found=True,
            status="failed",
            stage="verify",
            error="AssertionError: expected 1 got 2 in tests/test_foo.py",
        )
        verdict = classify_session(report, EnrichmentBundle())
        mock_llm.assert_called_once()
        self.assertEqual(verdict.case, "needs_fix")


if __name__ == "__main__":
    unittest.main()
