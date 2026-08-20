"""Unit tests for the answer layer (no live Cursor calls)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src.answer import REFUSAL_PHRASE, SYSTEM_PROMPT, _call_cursor_agent, _model_candidates, _parse_json_response, answer


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

    @patch("src.answer._call_cursor_agent")
    def test_answer_single_model_call_on_refusal(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": REFUSAL_PHRASE, "citations": []})
        chunks = [
            {
                "source": "faq -- https://foundry.mypaytm.com/faq",
                "text": "Run ark host enroll with your user API key.",
            }
        ]

        result = answer("how to enroll a host", chunks)

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        mock_cursor.assert_called_once()

    def test_model_candidates_deduplicates_haiku_variants(self) -> None:
        with patch.dict(os.environ, {"CURSOR_MODEL": "claude-haiku-4-5"}, clear=False):
            names = _model_candidates(None)
        self.assertEqual(names[0], "claude-haiku-4-5")
        self.assertNotIn("composer-2.5-fast", names)

    @patch("src.answer._call_cursor_sdk")
    @patch("src.answer._call_cursor_agent_cli")
    def test_auto_backend_prefers_sdk(
        self, mock_cli: MagicMock, mock_sdk: MagicMock
    ) -> None:
        mock_sdk.return_value = '{"answer":"ok","citations":[]}'
        with patch.dict(os.environ, {"CURSOR_ANSWER_BACKEND": "auto"}, clear=False):
            raw = _call_cursor_agent("test prompt", model="claude-haiku-4-5")
        self.assertIn("ok", raw)
        mock_sdk.assert_called_once()
        mock_cli.assert_not_called()

    def test_system_prompt_requires_answer_when_chunks_exist(self) -> None:
        self.assertIn("MUST synthesize an answer", SYSTEM_PROMPT)
        self.assertIn("Jira or", SYSTEM_PROMPT)

    def test_parse_json_response_strips_open_fence_without_closer(self) -> None:
        payload = {
            "answer": "A flow is a DAG of stages.",
            "citations": ["getting-started — https://foundry.mypaytm.com/onboarding/"],
        }
        raw = "```json\n" + json.dumps(payload)
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed["answer"], payload["answer"])
        self.assertEqual(parsed["citations"], payload["citations"])

    def test_parse_json_response_strips_full_fence_block(self) -> None:
        payload = {"answer": "ok", "citations": []}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed, payload)

    def test_parse_json_response_with_code_fences_inside_answer(self) -> None:
        payload = {
            "answer": "Run:\n```\nark flow create myteam-review --from flow.yaml\n```",
            "citations": ["getting-access — https://foundry.mypaytm.com/onboarding/getting-access"],
        }
        raw = "```json\n" + json.dumps(payload) + "\n```"
        parsed = _parse_json_response(raw)
        self.assertIn("ark flow create", parsed["answer"])
        self.assertEqual(len(parsed["citations"]), 1)

    def test_system_prompt_covers_workspace_ownership(self) -> None:
        self.assertIn("workspace ownership or sharing", SYSTEM_PROMPT)

    @patch("src.answer._call_cursor_agent")
    def test_workspace_ownership_answer_from_chunks(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps(
            {
                "answer": (
                    "Create your own workspace with ark workspace apply, "
                    "then reference it by name when you dispatch."
                ),
                "citations": ["getting-access — https://example.com/access"],
            }
        )
        chunks = [
            {
                "source": "getting-access — https://example.com/access",
                "text": "ark workspace apply myteam-workspace.yaml",
            }
        ]
        result = answer(
            "can I use someone else's workspace or do I need my own?",
            chunks,
        )
        self.assertNotEqual(result["answer"], REFUSAL_PHRASE)
        self.assertIn("workspace apply", result["answer"].lower())

    @patch("src.answer.shutil.which", return_value="/usr/bin/agent")
    def test_missing_api_key_raises(self, _mock_which: MagicMock) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        with self.assertRaises(ValueError):
            answer("test", [{"source": "s", "text": "t"}])


if __name__ == "__main__":
    unittest.main()
