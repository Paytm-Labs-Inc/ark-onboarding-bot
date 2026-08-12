"""Unit tests for ask() wiring (no live retrieval or Cursor calls)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.answer import REFUSAL_PHRASE
from src.ask import ask


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

        mock_retrieve.assert_called_once_with("how do I enroll a host?", k=5)
        mock_answer.assert_called_once_with("how do I enroll a host?", chunks)
        self.assertEqual(result["answer"], "Run ark host enroll.")
        self.assertEqual(len(result["citations"]), 1)

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


if __name__ == "__main__":
    unittest.main()
