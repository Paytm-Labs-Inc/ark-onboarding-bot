"""Tests for Scout RPC text normalisation."""

from __future__ import annotations

import unittest

from src.scout import ScoutReport, _normalize_read_text, _truncate


class ScoutNormalizeTests(unittest.TestCase):
    def test_session_echo_becomes_empty(self) -> None:
        payload = {"session": {"id": "s-abc1234567", "stage": "triage", "status": "failed"}}
        self.assertEqual(_normalize_read_text(payload), "")

    def test_lines_array(self) -> None:
        payload = {"lines": ["line one", "line two"]}
        self.assertEqual(_normalize_read_text(payload), "line one\nline two")

    def test_plain_string(self) -> None:
        self.assertEqual(_normalize_read_text("hello\nworld"), "hello\nworld")

    def test_truncate_none_is_empty(self) -> None:
        self.assertEqual(_truncate(None), "")

    def test_context_blob_omits_null_sections(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            session_summary="fix auth bug",
            runtime_list="",
            worktree_stat="",
        )
        blob = report.to_context_blob()
        self.assertNotIn("null", blob)
        self.assertNotIn("## Worktree", blob)
        self.assertNotIn("## Runtime", blob)


if __name__ == "__main__":
    unittest.main()
