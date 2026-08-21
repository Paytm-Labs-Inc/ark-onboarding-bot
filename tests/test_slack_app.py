"""Unit tests for the Slack front door (no live Slack or Cursor calls)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import threading

from src.slack_app import (
    PROMPT_HINT,
    answer_text,
    format_response,
    run_in_background,
    safe_answer,
    should_answer_dm,
    stream_answer_to_slack,
    strip_mention,
)


class SlackHelpersTests(unittest.TestCase):
    def test_strip_mention_removes_leading_bot_mention(self) -> None:
        self.assertEqual(strip_mention("<@U123ABC> how do I enroll?"), "how do I enroll?")
        self.assertEqual(strip_mention("no mention here"), "no mention here")

    def test_format_response_includes_citations(self) -> None:
        text = format_response(
            {"answer": "Run ark host enroll.", "citations": ["getting-started -- https://x"]}
        )
        self.assertIn("Run ark host enroll.", text)
        self.assertIn("*Sources*", text)
        self.assertIn("getting-started -- https://x", text)

    def test_format_response_without_citations(self) -> None:
        text = format_response({"answer": "I don't have that in the onboarding docs", "citations": []})
        self.assertEqual(text, "I don't have that in the onboarding docs")
        self.assertNotIn("*Sources*", text)

    def test_format_response_empty_answer_falls_back_to_hint(self) -> None:
        self.assertEqual(format_response({"answer": "", "citations": []}), PROMPT_HINT)

    @patch("src.slack_app.ask")
    def test_answer_text_forwards_stripped_question_to_ask(self, mock_ask) -> None:
        mock_ask.return_value = {"answer": "Use ~/.cursor/mcp.json", "citations": []}
        result = answer_text("<@U1> how do I set up Cursor?")
        mock_ask.assert_called_once_with("how do I set up Cursor?")
        self.assertIn("Use ~/.cursor/mcp.json", result)

    @patch("src.slack_app.ask")
    def test_answer_text_blank_question_hints_without_calling_ask(self, mock_ask) -> None:
        self.assertEqual(answer_text("<@U1>   "), PROMPT_HINT)
        mock_ask.assert_not_called()

    @patch("src.slack_app.ask", side_effect=ValueError("CURSOR_API_KEY not set"))
    def test_safe_answer_returns_message_instead_of_raising(self, _mock_ask) -> None:
        result = safe_answer("how do I enroll a host?")
        self.assertIn("hit an error", result)
        self.assertIn("CURSOR_API_KEY not set", result)

    def test_run_in_background_executes_target(self) -> None:
        done = threading.Event()
        run_in_background(done.set)
        self.assertTrue(done.wait(timeout=2))

    def test_should_answer_dm_only_for_human_ims(self) -> None:
        self.assertTrue(should_answer_dm({"channel_type": "im", "text": "hi"}))
        # Not a DM (channel message)
        self.assertFalse(should_answer_dm({"channel_type": "channel", "text": "hi"}))
        # Bot's own message
        self.assertFalse(should_answer_dm({"channel_type": "im", "bot_id": "B1"}))
        # Edited/system message
        self.assertFalse(should_answer_dm({"channel_type": "im", "subtype": "message_changed"}))

    @patch("src.slack_app.ask_stream")
    def test_stream_answer_to_slack_updates_message_in_place(self, mock_stream) -> None:
        mock_stream.return_value = iter(
            [
                {"type": "delta", "text": "Run ark host enroll."},
                {
                    "type": "done",
                    "answer": "Run ark host enroll.",
                    "citations": ["getting-started -- https://x"],
                },
            ]
        )
        client = MagicMock()
        stream_answer_to_slack(
            client,
            channel="C1",
            message_ts="123.456",
            raw_question="how do I enroll a host?",
        )
        self.assertGreaterEqual(client.chat_update.call_count, 1)
        final_text = client.chat_update.call_args_list[-1].kwargs["text"]
        self.assertIn("Run ark host enroll.", final_text)
        self.assertIn("*Sources*", final_text)


if __name__ == "__main__":
    unittest.main()
