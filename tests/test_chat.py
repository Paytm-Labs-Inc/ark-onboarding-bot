"""Tests for chat session memory."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.chat import (
    ChatSession,
    archive_session,
    ask_in_session,
    ask_in_session_stream,
    refresh_history_summary,
    save_session,
)


class ChatSessionTests(unittest.TestCase):
    @patch("src.chat.ask")
    def test_follow_up_passes_history(self, mock_ask) -> None:
        mock_ask.side_effect = [
            {
                "answer": "Open Settings and add MCP.",
                "citations": ["set-up-cursor -- https://x"],
                "retrieved_sources": ["set-up-cursor -- https://x"],
            },
            {
                "answer": "Put it in ~/.cursor/mcp.json.",
                "citations": ["set-up-cursor -- https://x"],
                "retrieved_sources": ["set-up-cursor -- https://x"],
            },
        ]

        first = ask_in_session(None, "how do I set up Cursor?")
        ask_in_session(first["session_id"], "where does the config file go?")

        self.assertEqual(mock_ask.call_count, 2)
        history = mock_ask.call_args_list[1].kwargs["history"]
        self.assertEqual(len(history), 1)
        self.assertIn("Cursor", history[0]["question"])

    def test_session_stores_all_turns_under_verbatim_cap(self) -> None:
        session = ChatSession()
        for index in range(6):
            session.add_turn(f"q{index}", f"a{index}", [], [])
        refresh_history_summary(session)
        self.assertEqual(len(session.turns), 6)
        self.assertEqual(len(session.history_for_prompt()), 6)
        self.assertEqual(session.history_summary, "")

    def test_long_thread_caps_verbatim_history_and_summarizes_older(self) -> None:
        with patch.dict(os.environ, {"MAX_HISTORY_TURNS": "12"}, clear=False):
            session = ChatSession()
            for index in range(15):
                session.add_turn(f"q{index}", f"a{index}", [], [])
            refresh_history_summary(session)
            self.assertEqual(len(session.turns), 15)
            history = session.history_for_prompt()
            self.assertEqual(len(history), 14)
            self.assertNotIn("q0", session.history_summary)
            self.assertIn("q1", session.history_summary)
            self.assertIn("q2", session.history_summary)
            self.assertNotIn("q14", session.history_summary)
            self.assertEqual(history[-1]["question"], "q14")
            self.assertEqual(history[0]["question"], "(Earlier conversation summary)")
            self.assertEqual(history[1]["question"], "q0")


class DegradedPassthroughTests(unittest.TestCase):
    """The web done event is rebuilt field by field, so degraded must be carried.

    Bugbot caught this on #37: the browser reads payload.degraded, but
    ask_in_session_stream dropped it, leaving the web half of the feature dead
    while Slack (which consumes ask_stream directly) worked.
    """

    def _events(self, done_extra: dict):
        def fake_stream(question, **kwargs):
            yield {"type": "delta", "text": "Run ark host enroll."}
            yield {
                "type": "done",
                "answer": "Run ark host enroll.",
                "citations": [],
                "retrieved_sources": ["getting-started"],
                **done_extra,
            }

        with patch("src.chat.ask_stream", side_effect=fake_stream):
            return list(ask_in_session_stream(None, "how do I enroll?"))

    def test_degraded_reaches_the_browser(self) -> None:
        done = self._events({"degraded": "salvaged"})[-1]
        self.assertEqual(done["degraded"], "salvaged")

    def test_clean_answer_has_no_degraded_key(self) -> None:
        done = self._events({})[-1]
        self.assertNotIn("degraded", done)



class HandoffFlagTests(unittest.TestCase):
    def _done(self, answer):
        def fake_stream(question, **kwargs):
            yield {"type": "done", "answer": answer, "citations": [], "retrieved_sources": []}
        with patch("src.chat.ask_stream", side_effect=fake_stream):
            return list(ask_in_session_stream(None, "q?"))[-1]

    def test_decline_sets_handoff(self) -> None:
        from src.answer import REFUSAL_PHRASE
        self.assertTrue(self._done(REFUSAL_PHRASE)["handoff"])

    def test_grounded_answer_does_not(self) -> None:
        self.assertFalse(self._done("Run ark host enroll.")["handoff"])


class NonStreamHandoffTests(unittest.TestCase):
    def test_ask_in_session_carries_handoff(self) -> None:
        from src.answer import REFUSAL_PHRASE
        with patch("src.chat.ask", return_value={"answer": REFUSAL_PHRASE, "citations": []}):
            self.assertTrue(ask_in_session(None, "q?")["handoff"])
        with patch("src.chat.ask", return_value={"answer": "Run ark host enroll.", "citations": []}):
            self.assertFalse(ask_in_session(None, "q?")["handoff"])


class ArchiveRaceTests(unittest.TestCase):
    """Archiving mid-stream must not be undone when the answer lands.

    ask_in_session_stream loads the ChatSession before generation. archive_session
    writes archived=True to the store while that object still has archived=False.
    The done-event save used to persist the stale object and put the chat back
    in Recent -- after the undo toast had already been shown.
    """

    def setUp(self) -> None:
        from src.session_store import MemorySessionStore, reset_session_store

        self.store = MemorySessionStore()
        reset_session_store(self.store)

    def tearDown(self) -> None:
        from src.session_store import reset_session_store

        reset_session_store(None)

    def test_save_after_concurrent_archive_keeps_the_chat_archived(self) -> None:
        session = ChatSession(session_id="sess-race", user_id="anonymous", title="what is ark?")
        session.add_turn("what is ark?", "Ark is...", [], [])
        save_session(session)

        in_flight = ChatSession(
            session_id="sess-race",
            user_id="anonymous",
            title="what is ark?",
            archived=False,
        )
        in_flight.add_turn("what is ark?", "Ark is...", [], [])
        in_flight.add_turn("follow up?", "More Ark.", [], [])

        self.assertTrue(archive_session("sess-race"))
        save_session(in_flight)

        reloaded = self.store.load("sess-race")
        self.assertIsNotNone(reloaded)
        self.assertTrue(reloaded.archived)
        self.assertIsNotNone(reloaded.archived_at)
        self.assertEqual(len(reloaded.turns), 2)
        self.assertEqual(reloaded.turns[-1].question, "follow up?")

    def test_finishing_a_stream_does_not_unarchive_a_concurrent_archive(self) -> None:
        with patch("src.chat.ask", return_value={"answer": "Ark is a platform.", "citations": []}):
            first = ask_in_session(None, "what is ark?")
        sid = first["session_id"]

        def fake_stream(question, **kwargs):
            yield {"type": "delta", "text": "More."}
            yield {
                "type": "done",
                "answer": "More about Ark.",
                "citations": [],
                "retrieved_sources": [],
            }

        with patch("src.chat.ask_stream", side_effect=fake_stream):
            events = ask_in_session_stream(sid, "tell me more")
            next(events)
            self.assertTrue(archive_session(sid))
            list(events)

        reloaded = self.store.load(sid)
        self.assertIsNotNone(reloaded)
        self.assertTrue(reloaded.archived)
        self.assertEqual(reloaded.turns[-1].answer, "More about Ark.")


if __name__ == "__main__":
    unittest.main()
