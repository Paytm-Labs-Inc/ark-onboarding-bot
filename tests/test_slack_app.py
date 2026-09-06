"""Unit tests for the Slack front door (no live Slack or Cursor calls)."""

from __future__ import annotations

import unittest
from unittest.mock import ANY, MagicMock, patch

import threading

from src.slack_app import (
    PROMPT_HINT,
    WORKING_NOTE,
    _setup_slash_command_stream,
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

    def test_format_response_flags_a_salvaged_answer(self) -> None:
        text = format_response(
            {"answer": "Run ark host enroll", "citations": [], "degraded": "salvaged"}
        )
        self.assertIn("connection dropped", text)
        self.assertIn("sources could not be recovered", text)

    def test_format_response_salvaged_with_citations_omits_source_wording(self) -> None:
        text = format_response(
            {
                "answer": "Run ark host enroll",
                "citations": ["getting-started — https://x"],
                "degraded": "salvaged",
            }
        )
        self.assertIn("connection dropped", text)
        self.assertNotIn("sources could not be recovered", text)

    def test_format_response_does_not_flag_a_clean_answer(self) -> None:
        text = format_response({"answer": "Run ark host enroll", "citations": []})
        self.assertNotIn("connection dropped", text)

    def test_format_response_does_not_flag_a_plain_fallback(self) -> None:
        # blocking-chunked still produced a complete answer -- saying so is noise.
        text = format_response(
            {
                "answer": "Run ark host enroll",
                "citations": [],
                "stream_mode": "blocking-chunked",
                "stream_errors": ["pi-stream: boom"],
            }
        )
        self.assertNotIn("connection dropped", text)

    def test_format_response_empty_answer_falls_back_to_hint(self) -> None:
        self.assertEqual(format_response({"answer": "", "citations": []}), PROMPT_HINT)

    @patch("src.slack_app.ask")
    def test_answer_text_forwards_stripped_question_to_ask(self, mock_ask) -> None:
        mock_ask.return_value = {"answer": "Use ~/.cursor/mcp.json", "citations": []}
        result = answer_text("<@U1> how do I set up Cursor?")
        mock_ask.assert_called_once_with("how do I set up Cursor?", channel="slack")
        self.assertIn("Use ~/.cursor/mcp.json", result)

    @patch("src.slack_app.ask")
    def test_answer_text_blank_question_hints_without_calling_ask(self, mock_ask) -> None:
        self.assertEqual(answer_text("<@U1>   "), PROMPT_HINT)
        mock_ask.assert_not_called()

    @patch("src.slack_app.ask", side_effect=ValueError("CURSOR_API_KEY not set"))
    def test_safe_answer_returns_message_instead_of_raising(self, _mock_ask) -> None:
        result = safe_answer("how do I enroll a host?")
        # The message is generic on purpose: the exception text names config
        # and hosts, which belong in the log, not the channel.
        self.assertIn("try again", result.lower())
        self.assertNotIn("CURSOR_API_KEY", result)

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
        update_message = MagicMock()
        stream_answer_to_slack(
            raw_question="how do I enroll a host?",
            update_message=update_message,
        )
        self.assertGreaterEqual(update_message.call_count, 1)
        final_text = update_message.call_args_list[-1].args[0]
        self.assertIn("Run ark host enroll.", final_text)
        self.assertIn("*Sources*", final_text)

    def test_setup_slash_command_stream_uses_chat_update_when_bot_is_in_channel(self) -> None:
        client = MagicMock()
        client.chat_postMessage.return_value = {"ts": "111.222"}
        respond = MagicMock()

        stream_target = _setup_slash_command_stream(
            client, respond, channel="C1", text=WORKING_NOTE
        )

        self.assertTrue(stream_target.incremental_updates)
        stream_target.update_message("updated answer")
        client.chat_postMessage.assert_called_once_with(channel="C1", text=WORKING_NOTE)
        client.chat_update.assert_called_once_with(
            channel="C1",
            ts="111.222",
            text="updated answer",
        )
        respond.assert_not_called()

    @patch("src.slack_app.ask_stream")
    def test_setup_slash_command_stream_uses_response_url_when_not_in_channel(
        self, mock_stream
    ) -> None:
        from slack_sdk.errors import SlackApiError

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
        client.chat_postMessage.side_effect = SlackApiError(
            message="not_in_channel",
            response={"error": "not_in_channel"},
        )
        respond = MagicMock()

        stream_target = _setup_slash_command_stream(
            client, respond, channel="C1", text=WORKING_NOTE
        )
        self.assertFalse(stream_target.incremental_updates)

        stream_answer_to_slack(
            raw_question="how do I enroll a host?",
            update_message=stream_target.update_message,
            incremental_updates=stream_target.incremental_updates,
        )

        respond.assert_any_call(text=WORKING_NOTE, response_type="in_channel")
        self.assertEqual(respond.call_count, 2)
        respond.assert_called_with(
            text=ANY,
            replace_original=True,
        )
        final_text = respond.call_args.kwargs["text"]
        self.assertIn("Run ark host enroll.", final_text)
        self.assertIn("*Sources*", final_text)
        client.chat_update.assert_not_called()



class HandoffLineTests(unittest.TestCase):
    def test_decline_carries_the_handoff_line(self) -> None:
        from src.answer import REFUSAL_PHRASE, ROADMAP_PHRASE
        for phrase in (REFUSAL_PHRASE, ROADMAP_PHRASE):
            self.assertIn("#foundry-users", format_response({"answer": phrase, "citations": []}))

    def test_grounded_answer_has_no_handoff_line(self) -> None:
        self.assertNotIn("#foundry-users", format_response({"answer": "Run ark host enroll", "citations": []}))


class HandoffOnSalvagedDeclineTests(unittest.TestCase):
    def test_a_decline_that_was_cut_short_still_carries_the_handoff(self) -> None:
        from src.answer import REFUSAL_PHRASE
        text = format_response({"answer": REFUSAL_PHRASE, "citations": [], "degraded": "salvaged"})
        self.assertIn("connection dropped", text)
        self.assertIn("#foundry-users", text)


class SlackErrorTextTests(unittest.TestCase):
    @patch("src.slack_app.ask", side_effect=RuntimeError("api.inference.paytm.com HTTP 503"))
    def test_gateway_error_text_is_not_posted_to_the_channel(self, _ask) -> None:
        text = safe_answer("how do I enroll?")
        self.assertNotIn("inference.paytm.com", text)
        self.assertIn("try again", text.lower())

if __name__ == "__main__":
    unittest.main()
