"""Unit tests for ask() wiring and CLI helpers (no live retrieval or Cursor calls)."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import MagicMock, patch

from src.answer import REFUSAL_PHRASE
from src.ask import ask, main, print_result, run_question


class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)

    @patch("src.ask.answer")
    @patch("src.ask.retrieve")
    def test_ask_wires_retrieve_to_answer(self, mock_retrieve: MagicMock, mock_answer: MagicMock) -> None:
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve.return_value = chunks
        mock_answer.return_value = {
            "answer": "Run ark host enroll.",
            "citations": ["getting-started -- https://example.com"],
        }

        result = ask("how do I enroll a host?")

        mock_retrieve.assert_called_once_with("how do I enroll a host?", k=8)
        mock_answer.assert_called_once_with(
            "how do I enroll a host?", chunks, history=None
        )
        self.assertEqual(result["answer"], "Run ark host enroll.")
        self.assertEqual(len(result["citations"]), 1)

    @patch("src.ask.answer")
    @patch("src.ask.retrieve")
    def test_ask_retrieval_includes_history_for_follow_ups(
        self, mock_retrieve: MagicMock, mock_answer: MagicMock
    ) -> None:
        chunks = [{"source": "set-up-cursor -- https://example.com", "text": "mcp.json"}]
        mock_retrieve.return_value = chunks
        mock_answer.return_value = {"answer": "Use ~/.cursor/mcp.json.", "citations": []}
        history = [{"question": "how do I set up Cursor?", "answer": "Use MCP."}]

        ask("where does the config file go?", history=history)

        query = mock_retrieve.call_args[0][0]
        self.assertIn("Cursor", query)
        self.assertIn("where does the config file go?", query)

    @patch("src.ask.answer")
    @patch("src.ask.retrieve")
    def test_ask_refuses_when_retrieval_empty(self, mock_retrieve: MagicMock, mock_answer: MagicMock) -> None:
        mock_retrieve.return_value = []

        result = ask("what is the meaning of life?")

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        self.assertEqual(result["citations"], [])
        mock_answer.assert_not_called()

    @patch("src.ask.answer")
    @patch("src.ask.retrieve")
    def test_ask_refuses_blank_question(self, mock_retrieve: MagicMock, mock_answer: MagicMock) -> None:
        result = ask("   ")

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        self.assertEqual(result["citations"], [])
        mock_retrieve.assert_not_called()
        mock_answer.assert_not_called()

    @patch("src.ask.answer")
    @patch("src.ask.retrieve")
    def test_run_question_skips_answer_when_no_chunks(
        self, mock_retrieve: MagicMock, mock_answer: MagicMock
    ) -> None:
        mock_retrieve.return_value = []

        result = run_question("unknown topic", verbose=False)

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        mock_answer.assert_not_called()

    def test_print_result_shows_refusal_heading(self) -> None:
        buffer = io.StringIO()
        print_result({"answer": REFUSAL_PHRASE, "citations": []}, file=buffer)
        output = buffer.getvalue()
        self.assertIn("Not in the docs", output)
        self.assertIn(REFUSAL_PHRASE, output)

    def test_print_result_shows_answer_and_sources(self) -> None:
        buffer = io.StringIO()
        print_result(
            {
                "answer": "Run ark host enroll.",
                "citations": ["getting-started -- https://example.com"],
            },
            file=buffer,
        )
        output = buffer.getvalue()
        self.assertIn("--- Answer ---", output)
        self.assertIn("Sources:", output)
        self.assertIn("getting-started -- https://example.com", output)

    @patch("src.ask.run_question")
    def test_main_one_shot_mode(self, mock_run: MagicMock) -> None:
        mock_run.return_value = {"answer": "ok", "citations": []}
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            code = main(["how", "do", "I", "enroll?"])
        self.assertEqual(code, 0)
        mock_run.assert_called_once_with("how do I enroll?")

    def test_main_one_shot_empty_question_exits_nonzero(self) -> None:
        code = main(["   "])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
