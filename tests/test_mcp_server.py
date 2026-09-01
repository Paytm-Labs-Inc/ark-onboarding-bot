"""Tests for the MCP wrapper: the HTTP call and formatting, no mcp package needed."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from src.mcp_server import AskFailed, ask_remote, format_answer


def _response(payload: dict) -> MagicMock:
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=body)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class AskRemoteTests(unittest.TestCase):
    def test_posts_the_question_and_returns_the_payload(self) -> None:
        opener = MagicMock(return_value=_response({"answer": "Run ark host enroll."}))
        result = ask_remote(
            "how do I enroll a host?", url="https://bot.example/x", token="t", opener=opener
        )
        self.assertEqual(result["answer"], "Run ark host enroll.")

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://bot.example/x/api/ask")
        self.assertEqual(json.loads(request.data)["question"], "how do I enroll a host?")
        self.assertEqual(request.get_header("Authorization"), "Bearer t")

    def test_no_token_sends_no_auth_header(self) -> None:
        opener = MagicMock(return_value=_response({"answer": "hi"}))
        ask_remote("q", url="https://bot.example", token="", opener=opener)
        self.assertIsNone(opener.call_args.args[0].get_header("Authorization"))

    def test_blank_question_never_reaches_the_network(self) -> None:
        opener = MagicMock()
        with self.assertRaises(AskFailed):
            ask_remote("   ", opener=opener)
        opener.assert_not_called()

    def test_401_explains_what_to_do_rather_than_showing_a_status(self) -> None:
        opener = MagicMock(
            side_effect=urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        )
        with self.assertRaises(AskFailed) as caught:
            ask_remote("q", url="https://bot.example", token="bad", opener=opener)
        self.assertIn("ARK_ACCESS_TOKEN", str(caught.exception))

    def test_unreachable_bot_names_the_url_it_tried(self) -> None:
        opener = MagicMock(side_effect=urllib.error.URLError("no route"))
        with self.assertRaises(AskFailed) as caught:
            ask_remote("q", url="https://bot.example", token="t", opener=opener)
        self.assertIn("bot.example", str(caught.exception))


class FormatAnswerTests(unittest.TestCase):
    def test_citations_are_appended_because_they_are_the_point(self) -> None:
        out = format_answer({"answer": "Do X.", "citations": ["getting-started -- https://e/x"]})
        self.assertIn("Do X.", out)
        self.assertIn("Sources:", out)
        self.assertIn("getting-started -- https://e/x", out)

    def test_no_citations_means_no_empty_sources_heading(self) -> None:
        self.assertEqual(format_answer({"answer": "Do X.", "citations": []}), "Do X.")

    def test_missing_answer_does_not_render_an_empty_bubble(self) -> None:
        self.assertIn("No answer returned", format_answer({}))
