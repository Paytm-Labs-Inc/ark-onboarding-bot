"""Tests for chat session memory."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.chat import ChatSession, ask_in_session


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


if __name__ == "__main__":
    unittest.main()
