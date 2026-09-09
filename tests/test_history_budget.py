"""Tests for model history token budget helpers."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.session_store import (
    StoredTurn,
    compile_history_summary,
    history_summary_max_chars,
    max_history_turns,
)


class HistoryBudgetTests(unittest.TestCase):
    def test_default_verbatim_cap_is_twelve(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MAX_HISTORY_TURNS", None)
            self.assertEqual(max_history_turns(), 12)

    def test_zero_falls_back_to_default_not_unlimited(self) -> None:
        with patch.dict(os.environ, {"MAX_HISTORY_TURNS": "0"}):
            self.assertEqual(max_history_turns(), 12)

    def test_negative_one_means_unlimited_verbatim(self) -> None:
        with patch.dict(os.environ, {"MAX_HISTORY_TURNS": "-1"}):
            self.assertEqual(max_history_turns(), 0)

    def test_compile_summary_skips_when_under_cap(self) -> None:
        turns = [
            StoredTurn(question=f"q{i}", answer=f"a{i}", citations=[], retrieved_sources=[])
            for i in range(5)
        ]
        self.assertEqual(
            compile_history_summary(turns, verbatim_cap=12, max_chars=3200),
            "",
        )

    def test_compile_summary_folds_older_turns(self) -> None:
        turns = [
            StoredTurn(question=f"q{i}", answer=f"a{i}", citations=[], retrieved_sources=[])
            for i in range(15)
        ]
        summary = compile_history_summary(turns, verbatim_cap=12, max_chars=3200)
        self.assertNotIn("q0", summary)
        self.assertIn("q1", summary)
        self.assertIn("q2", summary)
        self.assertNotIn("q12", summary)

    def test_compile_summary_truncates_to_char_cap(self) -> None:
        turns = [
            StoredTurn(
                question="x" * 200,
                answer="y" * 200,
                citations=[],
                retrieved_sources=[],
            )
            for _ in range(20)
        ]
        summary = compile_history_summary(turns, verbatim_cap=5, max_chars=500)
        self.assertLessEqual(len(summary), 500)
        self.assertTrue(summary.endswith("…"))

    def test_summary_max_chars_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("HISTORY_SUMMARY_MAX_CHARS", None)
            self.assertEqual(history_summary_max_chars(), 3200)


if __name__ == "__main__":
    unittest.main()
