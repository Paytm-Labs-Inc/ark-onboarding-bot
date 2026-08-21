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


class ChunkNumberCitationTests(unittest.TestCase):
    """chunks_used numbers map back to source labels (see _resolve_citations)."""

    CHUNKS = [
        {"source": "getting-started -- https://foundry.mypaytm.com/onboarding/", "text": "a"},
        {"source": "faq -- https://foundry.mypaytm.com/faq", "text": "b"},
        {"source": "set-up-cursor -- https://foundry.mypaytm.com/onboarding/cursor", "text": "c"},
    ]

    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)

    @patch("src.answer._call_cursor_agent")
    def test_numbers_map_to_sources(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": "Do the thing.", "chunks_used": [1, 3]})
        result = answer("q", self.CHUNKS)
        self.assertEqual(
            result["citations"], [self.CHUNKS[0]["source"], self.CHUNKS[2]["source"]]
        )

    @patch("src.answer._call_cursor_agent")
    def test_out_of_range_numbers_dropped(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": "Do the thing.", "chunks_used": [2, 9, 0, -1]})
        result = answer("q", self.CHUNKS)
        self.assertEqual(result["citations"], [self.CHUNKS[1]["source"]])

    @patch("src.answer._call_cursor_agent")
    def test_duplicate_numbers_deduped(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": "Do the thing.", "chunks_used": [2, 2, 1]})
        result = answer("q", self.CHUNKS)
        self.assertEqual(
            result["citations"], [self.CHUNKS[1]["source"], self.CHUNKS[0]["source"]]
        )

    @patch("src.answer._call_cursor_agent")
    def test_string_numbers_accepted(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": "Do the thing.", "chunks_used": ["1", "2"]})
        result = answer("q", self.CHUNKS)
        self.assertEqual(len(result["citations"]), 2)

    @patch("src.answer._call_cursor_agent")
    def test_refusal_has_no_citations(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": REFUSAL_PHRASE, "chunks_used": []})
        result = answer("q", self.CHUNKS)
        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        self.assertEqual(result["citations"], [])

    @patch("src.answer._call_cursor_agent")
    def test_verbatim_labels_still_accepted(self, mock_cursor: MagicMock) -> None:
        """Older responses and cached entries carry source labels, not numbers."""
        mock_cursor.return_value = json.dumps(
            {"answer": "Do the thing.", "citations": [self.CHUNKS[1]["source"]]}
        )
        result = answer("q", self.CHUNKS)
        self.assertEqual(result["citations"], [self.CHUNKS[1]["source"]])

    @patch("src.answer._call_cursor_agent")
    def test_unknown_label_dropped(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps(
            {"answer": "Do the thing.", "citations": ["invented -- https://example.com/nope"]}
        )
        result = answer("q", self.CHUNKS)
        self.assertEqual(result["citations"], [])

    def test_prompt_no_longer_leaks_source_into_chunk_header(self) -> None:
        from src.answer import _format_chunks

        formatted = _format_chunks(self.CHUNKS)
        self.assertIn("[Chunk 1]", formatted)
        self.assertNotIn("foundry.mypaytm.com", formatted)
        self.assertIn("chunks_used", SYSTEM_PROMPT)


class ReasoningModelParsingTests(unittest.TestCase):
    """Qwen3 and R1-family models emit a <think> block before the JSON payload."""

    def test_think_block_stripped(self) -> None:
        raw = (
            "<think>The user asks about hosts. Chunk {1} looks right, "
            "maybe {2} too.</think>\n"
            '{"answer": "Run `ark host enroll`.", "chunks_used": [1]}'
        )
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed["chunks_used"], [1])
        self.assertIn("ark host enroll", parsed["answer"])

    def test_think_block_with_fenced_payload(self) -> None:
        raw = '<think>reasoning {here}</think>\n```json\n{"answer": "ok", "chunks_used": []}\n```'
        self.assertEqual(_parse_json_response(raw)["answer"], "ok")

    def test_unterminated_think_block_raises(self) -> None:
        """Reasoning ran past max_tokens, so there is no payload to salvage."""
        with self.assertRaises((json.JSONDecodeError, ValueError)):
            _parse_json_response("<think>I should consider {chunk 1} and")
