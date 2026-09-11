"""Tests for Scout RPC text normalisation."""

from __future__ import annotations

import unittest

from src.scout import _normalize_read_text


class ScoutNormalizeTests(unittest.TestCase):
    def test_session_echo_becomes_empty(self) -> None:
        payload = {"session": {"id": "s-abc1234567", "stage": "triage", "status": "failed"}}
        self.assertEqual(_normalize_read_text(payload), "")

    def test_lines_array(self) -> None:
        payload = {"lines": ["line one", "line two"]}
        self.assertEqual(_normalize_read_text(payload), "line one\nline two")

    def test_plain_string(self) -> None:
        self.assertEqual(_normalize_read_text("hello\nworld"), "hello\nworld")


if __name__ == "__main__":
    unittest.main()
