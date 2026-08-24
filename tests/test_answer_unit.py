"""Unit tests for the answer layer (no live Cursor calls)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src.answer import (
    REFUSAL_PHRASE,
    ROADMAP_PHRASE,
    SYSTEM_PROMPT,
    _AnswerFieldStreamer,
    _call_cursor_agent,
    _model_candidates,
    _parse_json_response,
    answer,
    is_non_answer,
    stream_answer,
)


class AnswerLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"
        os.environ["ANSWER_BACKEND"] = "cursor"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        os.environ.pop("ANSWER_BACKEND", None)

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

    def test_answer_field_streamer_emits_incremental_text(self) -> None:
        streamer = _AnswerFieldStreamer()
        self.assertEqual(streamer.push('{"answer": "Run '), "Run ")
        self.assertEqual(streamer.push('ark host enroll."'), 'ark host enroll.')

    @patch("src.answer._generate_answer")
    @patch("src.answer._stream_cursor_agent_cli")
    @patch("src.answer._stream_cursor_sdk")
    def test_stream_answer_yields_deltas_then_done(
        self, mock_stream: MagicMock, mock_cli: MagicMock, mock_generate: MagicMock
    ) -> None:
        def fake_stream(_prompt: str, *, model: str):
            yield '{"answer": "Run '
            yield 'ark host enroll.", "citations": ["getting-started -- https://x"]}'
            return (
                '{"answer": "Run ark host enroll.", '
                '"citations": ["getting-started -- https://x"]}'
            )

        mock_stream.side_effect = fake_stream
        mock_cli.side_effect = AssertionError("CLI should not run when SDK streams")
        chunks = [
            {
                "source": "getting-started -- https://x",
                "text": "Run ark host enroll.",
            }
        ]
        events = list(stream_answer("how do I enroll?", chunks))
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "delta")
        self.assertIn("Run", events[0]["text"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("ark host enroll", events[-1]["answer"])
        self.assertEqual(events[-1].get("stream_mode"), "sdk")
        mock_generate.assert_not_called()

    @patch("src.answer._generate_answer")
    @patch("src.answer._stream_cursor_agent_cli", side_effect=RuntimeError("cli stream failed"))
    @patch("src.answer._stream_cursor_sdk", side_effect=RuntimeError("stream failed"))
    def test_stream_answer_falls_back_to_blocking(
        self, _mock_stream: MagicMock, _mock_cli: MagicMock, mock_generate: MagicMock
    ) -> None:
        mock_generate.return_value = {
            "answer": "Fallback answer.",
            "citations": ["getting-started -- https://x"],
        }
        chunks = [{"source": "getting-started -- https://x", "text": "t"}]
        events = list(stream_answer("question", chunks))
        self.assertGreater(len(events), 1)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["answer"], "Fallback answer.")
        self.assertEqual(events[-1].get("stream_mode"), "blocking-chunked")
        mock_generate.assert_called_once()

    def test_stream_answer_empty_chunks(self) -> None:
        events = list(stream_answer("question", []))
        self.assertEqual(
            events,
            [{"type": "done", "answer": REFUSAL_PHRASE, "citations": [], "stream_mode": "none"}],
        )


class ChunkNumberCitationTests(unittest.TestCase):
    """chunks_used numbers map back to source labels (see _resolve_citations)."""

    CHUNKS = [
        {"source": "getting-started -- https://foundry.mypaytm.com/onboarding/", "text": "a"},
        {"source": "faq -- https://foundry.mypaytm.com/faq", "text": "b"},
        {"source": "set-up-cursor -- https://foundry.mypaytm.com/onboarding/cursor", "text": "c"},
    ]

    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"
        os.environ["ANSWER_BACKEND"] = "cursor"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        os.environ.pop("ANSWER_BACKEND", None)

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


class PiInferenceBackendTests(unittest.TestCase):
    """The Pi Inference backend: an OpenAI-compatible completions POST."""

    CHUNKS = [{"source": "faq -- https://foundry.mypaytm.com/faq", "text": "Use a NAT gateway."}]

    def setUp(self) -> None:
        os.environ["ANSWER_BACKEND"] = "pi"
        os.environ["PI_API_KEY"] = "pi-test-key"

    def tearDown(self) -> None:
        for name in ("ANSWER_BACKEND", "PI_API_KEY", "PI_MODEL", "PI_BASE_URL", "PI_EXTRA_PARAMS"):
            os.environ.pop(name, None)

    @staticmethod
    def _response(payload: dict, status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(payload)}}]
        }
        resp.text = json.dumps(payload)
        return resp

    @patch("src.answer.httpx.post")
    def test_answer_uses_pi_backend(self, mock_post: MagicMock) -> None:
        mock_post.return_value = self._response({"answer": "Use a NAT gateway.", "chunks_used": [1]})
        result = answer("how do I fix the jira block?", self.CHUNKS)

        self.assertEqual(result["citations"], [self.CHUNKS[0]["source"]])
        url = mock_post.call_args.args[0]
        self.assertTrue(url.endswith("/v1/chat/completions"))
        self.assertIn("api.inference.paytm.com", url)

    @patch("src.answer.httpx.post")
    def test_qwen_is_the_default_model_and_carries_its_params(self, mock_post: MagicMock) -> None:
        """Qwen3 needs reasoning disabled and JSON mode or it emits a <think> block."""
        mock_post.return_value = self._response({"answer": "ok", "chunks_used": []})
        answer("q", self.CHUNKS)

        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "qwen/qwen3-32b")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["response_format"], {"type": "json_object"})

    @patch("src.answer.httpx.post")
    def test_model_without_special_params_sends_none(self, mock_post: MagicMock) -> None:
        os.environ["PI_MODEL"] = "Claude Haiku 4.5"
        mock_post.return_value = self._response({"answer": "ok", "chunks_used": []})
        answer("q", self.CHUNKS)

        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "Claude Haiku 4.5")
        self.assertNotIn("reasoning_effort", body)

    @patch("src.answer.httpx.post")
    def test_pi_extra_params_override(self, mock_post: MagicMock) -> None:
        os.environ["PI_EXTRA_PARAMS"] = '{"temperature": 0, "reasoning_effort": "default"}'
        mock_post.return_value = self._response({"answer": "ok", "chunks_used": []})
        answer("q", self.CHUNKS)

        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["reasoning_effort"], "default")

    def test_missing_key_names_the_variable_and_the_escape_hatch(self) -> None:
        os.environ.pop("PI_API_KEY", None)
        with self.assertRaises(ValueError) as ctx:
            answer("q", self.CHUNKS)
        self.assertIn("PI_API_KEY", str(ctx.exception))
        self.assertIn("ANSWER_BACKEND=cursor", str(ctx.exception))

    @patch("src.answer.httpx.post")
    def test_http_error_surfaces_status_and_model(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 404
        resp.text = '{"error":{"message":"Model not found"}}'
        mock_post.return_value = resp
        with self.assertRaises(RuntimeError) as ctx:
            answer("q", self.CHUNKS)
        self.assertIn("404", str(ctx.exception))
        self.assertIn("qwen/qwen3-32b", str(ctx.exception))

    def test_unknown_backend_is_rejected(self) -> None:
        os.environ["ANSWER_BACKEND"] = "banana"
        with self.assertRaises(ValueError) as ctx:
            answer("q", self.CHUNKS)
        self.assertIn("banana", str(ctx.exception))

    @patch("src.answer._call_cursor_agent")
    def test_warm_agent_is_a_noop_on_pi(self, mock_cursor: MagicMock) -> None:
        from src.answer import warm_agent

        os.environ["CURSOR_API_KEY"] = "crsr_test_key"
        warm_agent()
        mock_cursor.assert_not_called()
        os.environ.pop("CURSOR_API_KEY", None)


class DeclineWordingTests(unittest.TestCase):
    """Out-of-scope declines and not-yet-documented declines read differently."""

    CHUNKS = [{"source": "faq -- https://foundry.mypaytm.com/faq", "text": "x"}]

    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"
        os.environ["ANSWER_BACKEND"] = "cursor"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        os.environ.pop("ANSWER_BACKEND", None)

    def test_both_phrases_count_as_non_answers(self) -> None:
        self.assertTrue(is_non_answer(REFUSAL_PHRASE))
        self.assertTrue(is_non_answer(ROADMAP_PHRASE))
        self.assertTrue(is_non_answer(f"  {ROADMAP_PHRASE}  "))
        self.assertFalse(is_non_answer("Run `ark host enroll`."))

    def test_prompt_gives_the_model_both_exact_strings(self) -> None:
        self.assertIn(REFUSAL_PHRASE, SYSTEM_PROMPT)
        self.assertIn(ROADMAP_PHRASE, SYSTEM_PROMPT)

    @patch("src.answer._call_cursor_agent")
    def test_roadmap_reply_carries_no_citations(self, mock_cursor: MagicMock) -> None:
        mock_cursor.return_value = json.dumps({"answer": ROADMAP_PHRASE, "chunks_used": []})
        result = answer("does ark do X?", self.CHUNKS)
        self.assertEqual(result["answer"], ROADMAP_PHRASE)
        self.assertEqual(result["citations"], [])

    def test_query_log_counts_a_roadmap_reply_as_refused(self) -> None:
        from src.query_log import is_refused

        self.assertTrue(is_refused(ROADMAP_PHRASE))
        self.assertTrue(is_refused(REFUSAL_PHRASE))
        self.assertFalse(is_refused("Use `ark workspace apply`."))




class PiRetryTests(unittest.TestCase):
    """Groq's JSON mode intermittently 400s; that is retryable, config errors are not."""

    CHUNKS = [{"source": "faq -- https://foundry.mypaytm.com/faq", "text": "x"}]

    def setUp(self) -> None:
        os.environ["ANSWER_BACKEND"] = "pi"
        os.environ["PI_API_KEY"] = "pi-test-key"
        os.environ["PI_MAX_ATTEMPTS"] = "3"

    def tearDown(self) -> None:
        for name in ("ANSWER_BACKEND", "PI_API_KEY", "PI_MAX_ATTEMPTS"):
            os.environ.pop(name, None)

    @staticmethod
    def _resp(status, payload=None, text=""):
        r = MagicMock()
        r.status_code = status
        r.text = text or json.dumps(payload or {})
        r.json.return_value = (
            {"choices": [{"message": {"content": json.dumps(payload)}}]} if payload else {}
        )
        return r

    def test_classifier(self) -> None:
        from src.answer import _pi_is_retryable

        self.assertTrue(_pi_is_retryable(429, ""))
        self.assertTrue(_pi_is_retryable(503, ""))
        self.assertTrue(_pi_is_retryable(400, '{"error":{"message":"Failed to generate JSON"}}'))
        self.assertTrue(_pi_is_retryable(400, '{"failed_generation":"..."}'))
        self.assertFalse(_pi_is_retryable(400, '{"error":{"message":"bad model"}}'))
        self.assertFalse(_pi_is_retryable(401, ""))
        self.assertFalse(_pi_is_retryable(404, '{"error":{"message":"Model not found"}}'))

    @patch("src.answer.time.sleep", lambda *_: None)
    @patch("src.answer.httpx.post")
    def test_json_mode_failure_is_retried_and_succeeds(self, mock_post: MagicMock) -> None:
        good = {"answer": "Use a NAT gateway.", "chunks_used": [1]}
        mock_post.side_effect = [
            self._resp(400, text='{"error":{"message":"Failed to generate JSON"}}'),
            self._resp(200, good),
        ]
        result = answer("q", self.CHUNKS)
        self.assertEqual(result["citations"], [self.CHUNKS[0]["source"]])
        self.assertEqual(mock_post.call_count, 2)

    @patch("src.answer.time.sleep", lambda *_: None)
    @patch("src.answer.httpx.post")
    def test_rate_limit_is_retried(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = [
            self._resp(429, text="rate limited"),
            self._resp(200, {"answer": "ok", "chunks_used": []}),
        ]
        answer("q", self.CHUNKS)
        self.assertEqual(mock_post.call_count, 2)

    @patch("src.answer.time.sleep", lambda *_: None)
    @patch("src.answer.httpx.post")
    def test_config_error_is_not_retried(self, mock_post: MagicMock) -> None:
        mock_post.return_value = self._resp(404, text='{"error":{"message":"Model not found"}}')
        with self.assertRaises(RuntimeError):
            answer("q", self.CHUNKS)
        self.assertEqual(mock_post.call_count, 1)

    @patch("src.answer.time.sleep", lambda *_: None)
    @patch("src.answer.httpx.post")
    def test_gives_up_after_max_attempts(self, mock_post: MagicMock) -> None:
        mock_post.return_value = self._resp(503, text="upstream down")
        with self.assertRaises(RuntimeError) as ctx:
            answer("q", self.CHUNKS)
        self.assertEqual(mock_post.call_count, 3)
        self.assertIn("on 3 attempt(s)", str(ctx.exception))

    @patch("src.answer.time.sleep", lambda *_: None)
    @patch("src.answer.httpx.post")
    def test_single_attempt_failure_omits_the_count(self, mock_post: MagicMock) -> None:
        """A config mistake fails on the first try, so no attempt count is added."""
        mock_post.return_value = self._resp(404, text='{"error":{"message":"Model not found"}}')
        with self.assertRaises(RuntimeError) as ctx:
            answer("q", self.CHUNKS)
        self.assertNotIn("attempt", str(ctx.exception))

    @patch("src.answer.time.sleep", lambda *_: None)
    @patch("src.answer.httpx.post")
    def test_exhausted_network_error_reports_attempts(self, mock_post: MagicMock) -> None:
        """The count must appear on the network path too, not just on status codes."""
        import httpx as _httpx

        mock_post.side_effect = _httpx.ConnectError("connection refused")
        with self.assertRaises(RuntimeError) as ctx:
            answer("q", self.CHUNKS)
        self.assertIn("attempt(s)", str(ctx.exception))




class PiStreamFrameSizeTests(unittest.TestCase):
    """The existing streamer, driven at the frame sizes an SSE stream produces."""

    PAYLOAD = (
        '{"answer": "Run `ark host enroll`.\\nThen check \\"status\\" done.",'
        ' "chunks_used": [1, 3]}'
    )

    def test_matches_json_at_every_frame_size(self) -> None:
        from src.answer import _AnswerFieldStreamer

        expected = json.loads(self.PAYLOAD)["answer"]
        for size in (1, 2, 5, 7, 13, len(self.PAYLOAD)):
            st = _AnswerFieldStreamer()
            out = "".join(
                st.push(self.PAYLOAD[i : i + size])
                for i in range(0, len(self.PAYLOAD), size)
            )
            self.assertEqual(out, expected, f"frame size {size}")

    def test_emits_nothing_before_the_field_appears(self) -> None:
        from src.answer import _AnswerFieldStreamer

        self.assertEqual(_AnswerFieldStreamer().push('{"chunks_'), "")


class PiStreamFallbackGuardTests(unittest.TestCase):
    """A parse failure falls back to blocking only when no delta was emitted.

    The Pi stream drops JSON mode, so an unparseable payload is an expected
    tail case. Once deltas have streamed, falling back would replay the whole
    answer and the Slack preview (which accumulates deltas) renders it twice.
    """

    CHUNKS = [{"source": "getting-started -- https://x", "text": "Run ark host enroll."}]

    def setUp(self) -> None:
        os.environ["ANSWER_BACKEND"] = "pi"

    def tearDown(self) -> None:
        os.environ.pop("ANSWER_BACKEND", None)

    @patch("src.answer._generate_answer")
    @patch("src.answer._stream_pi_inference")
    def test_parse_failure_after_deltas_does_not_replay(
        self, mock_stream: MagicMock, mock_generate: MagicMock
    ) -> None:
        def fake_stream(_prompt: str):
            yield '{"answer": "Run ark host enroll.'
            yield ' Then dispatch a session."'
            yield ', "chunks_used": [1'  # truncated: the full payload never parses
            return (
                '{"answer": "Run ark host enroll. Then dispatch a session."'
                ', "chunks_used": [1'
            )

        mock_stream.side_effect = fake_stream
        mock_generate.return_value = {"answer": "Fallback answer.", "citations": []}
        events = list(stream_answer("how do I enroll?", self.CHUNKS))

        deltas = "".join(e["text"] for e in events if e["type"] == "delta")
        dones = [e for e in events if e["type"] == "done"]
        self.assertEqual(deltas, "Run ark host enroll. Then dispatch a session.")
        self.assertEqual(deltas.count("Run ark host enroll"), 1)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stream_mode"], "pi-stream")
        self.assertEqual(
            dones[0]["answer"], "Run ark host enroll. Then dispatch a session."
        )
        mock_generate.assert_not_called()

    @patch("src.answer._generate_answer")
    @patch("src.answer._stream_pi_inference")
    def test_parse_failure_with_zero_deltas_still_falls_back(
        self, mock_stream: MagicMock, mock_generate: MagicMock
    ) -> None:
        def fake_stream(_prompt: str):
            yield "not json, and no answer field"
            return "not json, and no answer field"

        mock_stream.side_effect = fake_stream
        mock_generate.return_value = {"answer": "Fallback answer.", "citations": []}
        events = list(stream_answer("question", self.CHUNKS))

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["answer"], "Fallback answer.")
        self.assertEqual(events[-1]["stream_mode"], "blocking-chunked")
        self.assertTrue(events[-1]["stream_errors"])
        mock_generate.assert_called_once()


class CursorCliStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"
        os.environ["CURSOR_ANSWER_BACKEND"] = "cli"
        os.environ["CURSOR_TIMEOUT_SECONDS"] = "0"

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        os.environ.pop("CURSOR_ANSWER_BACKEND", None)
        os.environ.pop("CURSOR_TIMEOUT_SECONDS", None)

    @patch("src.answer.subprocess.Popen")
    @patch("src.answer.shutil.which", return_value="/usr/bin/agent")
    def test_stream_cursor_agent_cli_times_out_when_stdout_is_silent(
        self, _mock_which: MagicMock, mock_popen: MagicMock
    ) -> None:
        import os as _os

        from src.answer import _stream_cursor_agent_cli

        read_fd, write_fd = _os.pipe()
        _os.close(write_fd)
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = read_fd
        mock_stdout.read.return_value = ""
        mock_stderr = MagicMock()
        mock_stderr.__iter__ = MagicMock(return_value=iter([]))
        proc = MagicMock()
        proc.stdout = mock_stdout
        proc.stderr = mock_stderr
        proc.poll.return_value = None
        proc.returncode = None
        mock_popen.return_value = proc

        with self.assertRaises(TimeoutError):
            list(_stream_cursor_agent_cli("prompt", model="auto"))

        proc.kill.assert_called_once()
        _os.close(read_fd)


if __name__ == "__main__":
    unittest.main()
