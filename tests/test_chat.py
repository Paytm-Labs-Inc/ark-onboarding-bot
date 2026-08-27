"""Tests for chat session memory."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.chat import ChatSession, ask_in_session, ask_in_session_stream


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

    def test_session_keeps_recent_turns_only(self) -> None:
        session = ChatSession()
        for index in range(6):
            session.add_turn(f"q{index}", f"a{index}", [], [])
        self.assertEqual(len(session.turns), 4)


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

if __name__ == "__main__":
    unittest.main()
