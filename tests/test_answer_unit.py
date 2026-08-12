"""Unit tests for the answer layer (no live Cursor calls)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src.answer import REFUSAL_PHRASE, answer


class AnswerLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)

    @patch("src.answer._call_cursor_agent")
    def test_answer_returns_parsed_json(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps(
            {
                "answer": "Run ark host enroll <compute-name>.",
                "citations": [
                    "getting-started — https://foundry.mypaytm.com/onboarding/getting-started"
                ],
            }
        )

        chunks = [
            {
                "source": "getting-started — https://foundry.mypaytm.com/onboarding/getting-started",
                "text": "Run ark host enroll <compute-name>.",
            }
        ]
        result = answer("how do I enroll a host?", chunks)

        self.assertIn("ark host enroll", result["answer"])
        self.assertEqual(len(result["citations"]), 1)
        mock_cursor.assert_called_once()

    @patch("src.answer._call_cursor_agent")
    def test_refusal_when_chunks_empty(self, mock_cursor: MagicMock) -> None:
        result = answer("how do I enroll a host?", [])
        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        self.assertEqual(result["citations"], [])
        mock_cursor.assert_not_called()

    @patch("src.answer.shutil.which", return_value="/usr/bin/agent")
    def test_missing_api_key_raises(self, _mock_which: MagicMock) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        with self.assertRaises(ValueError):
            answer("test", [{"source": "s", "text": "t"}])


if __name__ == "__main__":
    unittest.main()
