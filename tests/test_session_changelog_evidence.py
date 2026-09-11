"""Tests for Case 1 changelog evidence gate."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.scout import ScoutReport
from src.session_changelog_evidence import (
    _error_needle,
    apply_already_fixed_gate,
    find_strong_changelog_match,
)
from src.session_enrichers import CHANGELOG_UNAVAILABLE, EnrichmentBundle
from src.session_verdict import DebugVerdict


class ChangelogEvidenceTests(unittest.TestCase):
    def test_error_substring_match(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="ResolveMessage: Cannot find module './986.js'",
            stage="triage",
        )
        enrichment = EnrichmentBundle(
            changelog_hits=[
                {
                    "ref": "deadbeef",
                    "subject": "fix Cannot find module './986.js' in arkd bundle",
                    "date": "2026-01-01",
                }
            ]
        )
        match = find_strong_changelog_match(report, enrichment)
        self.assertIsNotNone(match)
        self.assertEqual(match.match_type, "error_substring")

    def test_changelog_unavailable_downgrades_already_fixed(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="some unrelated timeout",
            stage="verify",
        )
        verdict = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Looks fixed",
            root_cause="timeout",
            evidence=["llm guess"],
            matching_fix_ref="abc",
        )
        gated = apply_already_fixed_gate(
            verdict,
            report,
            EnrichmentBundle(changelog_note=CHANGELOG_UNAVAILABLE),
        )
        self.assertEqual(gated.case, "needs_fix")
        self.assertIn("Changelog evidence unavailable", gated.evidence[0])

    def test_no_match_downgrades_already_fixed(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="some unrelated timeout",
            stage="verify",
        )
        verdict = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Looks fixed",
            root_cause="timeout",
            evidence=["llm guess"],
            matching_fix_ref="abc",
        )
        gated = apply_already_fixed_gate(verdict, report, EnrichmentBundle())
        self.assertEqual(gated.case, "needs_fix")
        self.assertIn("No strong changelog match", gated.evidence[0])

    def test_generic_needle_rejected_before_min_length(self) -> None:
        self.assertEqual(_error_needle("Error: unknown"), "")
        self.assertEqual(_error_needle("fatal: not found"), "")

    def test_weak_needle_after_prefix_strip_rejected(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="Error: failed",
            stage="verify",
        )
        enrichment = EnrichmentBundle(
            changelog_hits=[
                {
                    "ref": "deadbeef",
                    "subject": "fix unrelated failed deploy",
                    "date": "2026-01-01",
                }
            ]
        )
        self.assertIsNone(find_strong_changelog_match(report, enrichment))

    def test_rejected_already_fixed_becomes_needs_fix(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="some unrelated timeout",
            stage="verify",
        )
        verdict = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Looks fixed",
            root_cause="timeout",
            evidence=["llm guess"],
            matching_fix_ref="abc",
        )
        gated = apply_already_fixed_gate(verdict, report, EnrichmentBundle())
        self.assertEqual(gated.case, "needs_fix")

    @patch.dict("os.environ", {}, clear=True)
    def test_argocd_skipped_when_unconfigured(self) -> None:
        from src.argocd_client import format_deployment_status

        self.assertEqual(format_deployment_status("abc123"), "")


if __name__ == "__main__":
    unittest.main()
