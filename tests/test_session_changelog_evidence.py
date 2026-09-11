"""Tests for Case 1 changelog evidence gate."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.scout import ScoutReport
from src.session_changelog_evidence import (
    apply_already_fixed_gate,
    find_strong_changelog_match,
)
from src.session_classifier import DebugVerdict
from src.session_enrichers import CHANGELOG_UNAVAILABLE, EnrichmentBundle
from src.session_handlers import handle_already_fixed


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
        self.assertNotEqual(gated.case, "already_fixed")
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
        self.assertNotEqual(gated.case, "already_fixed")
        self.assertIn("No strong changelog match", gated.evidence[0])

    def test_handler_leads_with_live_failure(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="AssertionError in tests/test_foo.py",
            stage="verify",
        )
        verdict = DebugVerdict(
            case="already_fixed",
            confidence=0.9,
            summary="Fixed on main",
            root_cause="bad assertion",
            evidence=["deadbeef: fix test_foo (matched file_path: tests/test_foo.py)"],
            matching_fix_ref="deadbeef",
        )
        text = handle_already_fixed(report, verdict, EnrichmentBundle())
        self.assertIn("What failed (this session):", text)
        self.assertIn("AssertionError", text)
        self.assertIn("still failed", text.lower())

    @patch.dict("os.environ", {}, clear=True)
    def test_argocd_skipped_when_unconfigured(self) -> None:
        from src.argocd_client import format_deployment_status

        self.assertEqual(format_deployment_status("abc123"), "")


if __name__ == "__main__":
    unittest.main()
