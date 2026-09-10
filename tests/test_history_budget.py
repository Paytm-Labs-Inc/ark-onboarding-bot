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
        # Truncates from front to preserve recent turns (coreference).
        self.assertTrue(summary.startswith("…"))

    def test_summary_max_chars_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("HISTORY_SUMMARY_MAX_CHARS", None)
            self.assertEqual(history_summary_max_chars(), 3200)


if __name__ == "__main__":
    unittest.main()


class HistoryBudgetIsWiredToTheSavePathTests(unittest.TestCase):
    """The budget has to be reachable from the app, not just from its helpers.

    Every other test here calls compile_history_summary/refresh_history_summary
    directly, so the feature could be unplugged from the application and the
    suite would not notice. It was: deleting refresh_history_summary() from
    save_session, or dropping history_summary from StoredSession.from_dict so
    it never survives a reload, each left all 337 tests green.

    This drives save_session and reads the session back out of the store, so it
    fails on either mutation.
    """

    def setUp(self) -> None:
        from src.session_store import MemorySessionStore, reset_session_store

        os.environ["MAX_HISTORY_TURNS"] = "3"
        self.store = MemorySessionStore()
        reset_session_store(self.store)

    def tearDown(self) -> None:
        from src.session_store import reset_session_store

        os.environ.pop("MAX_HISTORY_TURNS", None)
        reset_session_store(None)

    def test_saving_a_long_session_persists_a_summary_that_keeps_turn_zero_out(self) -> None:
        from src.chat import ChatSession, save_session
        from src.session_store import StoredTurn

        session = ChatSession(session_id="s-budget-1", user_id="browseraaaaaaaaaa")
        session.turns = [
            StoredTurn(
                question=f"question number {i}",
                answer=f"answer number {i}",
                citations=[],
                retrieved_sources=[],
            )
            for i in range(15)
        ]

        save_session(session)

        reloaded = self.store.load("s-budget-1")
        self.assertIsNotNone(reloaded)
        # Non-empty: the summary was compiled AND survived the round trip.
        self.assertTrue(reloaded.history_summary)
        # Turn 0 is deliberately held out of the summary -- it carries the
        # constraints ("I'm on OCL") that folding oldest-first would lose.
        self.assertNotIn("question number 0", reloaded.history_summary)
        # A middle turn is in it, so this is the summary and not some other string.
        self.assertIn("question number 5", reloaded.history_summary)

        # MemorySessionStore hands back the same object, so the assertions above
        # never cross to_dict/from_dict -- which is where a redis-backed store
        # actually reads it. Pin that leg explicitly, or dropping the field from
        # from_dict passes every test while the summary silently stops surviving
        # a reload in the only backend that serialises.
        from src.session_store import StoredSession

        round_tripped = StoredSession.from_dict(reloaded.to_dict())
        self.assertEqual(round_tripped.history_summary, reloaded.history_summary)
