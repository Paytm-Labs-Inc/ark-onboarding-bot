"""Tests for structured session diagnosis timelines."""

from __future__ import annotations

import unittest

from src.scout import ScoutReport
from src.session_timeline import build_timeline, prepend_timeline, timeline_to_markdown


class SessionTimelineTests(unittest.TestCase):
    def test_infra_failure_timeline(self) -> None:
        report = ScoutReport(
            session_id="s-hhg59veu8a",
            found=True,
            status="failed",
            stage="triage",
            failed_stage="triage",
            error="ResolveMessage: Cannot find module './986.js' from '/$bunfs/root/ark-darwin-arm64'",
            session_summary="Task 2 ask()+CLI",
            flow_name="ark-feature",
            workspace_name="modeltest-modeltest-ark-onboarding-bot",
            compute_name="aneetta-mac",
            raw_events=[
                {"type": "stage_started", "stage": "triage"},
                {"type": "session_failed", "message": "ResolveMessage"},
            ],
        )
        timeline = build_timeline(report)
        markdown = timeline_to_markdown(timeline)

        self.assertIn("Session s-hhg59veu8a", markdown)
        self.assertIn("986.js", markdown)
        self.assertIn("What happened:", markdown)
        self.assertIn("modeltest-modeltest-ark-onboarding-bot", markdown)

    def test_action_results_rendered(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            status="failed",
            stage="verify",
            failed_stage="verify",
            error="verify-workdir failed",
            raw_action_results=[
                {
                    "action": "verify-workdir",
                    "ok": False,
                    "message": "missing repos/foo",
                    "exit_code": 1,
                }
            ],
        )
        timeline = build_timeline(report)
        markdown = timeline_to_markdown(timeline)

        self.assertIn("verify-workdir", markdown)
        self.assertIn("failed", markdown)
        self.assertIn("missing repos/foo", markdown)

    def test_prepend_timeline(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            status="failed",
            stage="verify",
            error="boom",
        )
        combined = prepend_timeline(report, "Ask Ark cannot fix this automatically.\n\nDetails here.")
        self.assertIn("Session s-abc1234567", combined)
        self.assertIn("cannot fix this automatically", combined.lower())

    def test_synthetic_event_trail_when_events_missing(self) -> None:
        report = ScoutReport(
            session_id="s-ab8cv8x0ic",
            found=True,
            status="failed",
            stage="triage",
            failed_stage="triage",
            error="ResolveMessage: Cannot find module './986.js'",
            compute_name="aneetta-mac",
            raw_show={
                "agent": "ark-triager",
                "workspace_runtime_id": "wsr-0ed4b765-7687-4488-b3f2-dbac7d54944e",
                "created_at": "2026-08-12T10:14:52.805Z",
                "ended_at": "2026-08-12T10:15:09.426Z",
            },
        )
        markdown = timeline_to_markdown(build_timeline(report))
        self.assertIn("Session marked failed", markdown)
        self.assertIn("ark-triager", markdown)
        self.assertIn("wsr-0ed4b765", markdown)
        self.assertNotIn('"session": {', markdown)

    def test_timeline_dict_shape(self) -> None:
        from src.session_timeline import timeline_to_dict

        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            status="failed",
            stage="verify",
            error="boom",
            gather_errors=["worktree: unavailable"],
        )
        payload = timeline_to_dict(build_timeline(report))
        self.assertIn("headline", payload)
        self.assertIn("sections", payload)
        self.assertEqual(payload["warnings"], ["worktree: unavailable"])


if __name__ == "__main__":
    unittest.main()
