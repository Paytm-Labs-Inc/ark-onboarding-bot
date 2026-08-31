"""Unit tests for ask() wiring and CLI helpers (no live retrieval or Cursor calls)."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import MagicMock, patch

from src.answer import REFUSAL_PHRASE
from src.ask import ask, clear_answer_cache, clear_retrieval_cache, main, print_result, run_question
from src.ask import _normalize_cache_key
from src.retrieve import RetrievalResult


class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test_key"
        clear_retrieval_cache()
        clear_answer_cache()

    def tearDown(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        os.environ.pop("ASK_RETRIEVAL_CACHE", None)
        os.environ.pop("ASK_ANSWER_CACHE", None)
        clear_retrieval_cache()
        clear_answer_cache()

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_wires_retrieve_to_answer(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        clear_retrieval_cache()
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.82)
        mock_answer.return_value = {
            "answer": "Run ark host enroll.",
            "citations": ["getting-started -- https://example.com"],
        }

        result = ask("how do I enroll a host?")

        mock_retrieve_scored.assert_called_once_with("how do I enroll a host?", k=8)
        mock_answer.assert_called_once_with(
            "how do I enroll a host?", chunks, history=None
        )
        self.assertEqual(result["answer"], "Run ark host enroll.")
        self.assertEqual(len(result["citations"]), 1)
        _mock_log.assert_called_once()

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_second_identical_ask_is_logged_as_a_cache_hit(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, mock_log: MagicMock
    ) -> None:
        """A cached reply and a real model call must be distinguishable in the log.

        Without this they write identical records, so model-call volume, cost and
        the real latency distribution are all unrecoverable afterwards.
        """
        clear_retrieval_cache()
        clear_answer_cache()
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.82)
        mock_answer.return_value = {
            "answer": "Run ark host enroll.",
            "citations": ["getting-started -- https://example.com"],
        }

        ask("how do I enroll a host?")
        ask("how do I enroll a host?")

        # The model is called once; the log records two answers.
        self.assertEqual(mock_answer.call_count, 1)
        self.assertEqual(mock_log.call_count, 2)

        first, second = (call.kwargs for call in mock_log.call_args_list)
        self.assertFalse(first["cache_hit"], "first ask went to the model")
        self.assertTrue(second["cache_hit"], "second ask was served from cache")

        # Every record carries a duration and a correlation id, and the two asks
        # are separate requests even though the answer is identical.
        for record in (first, second):
            self.assertIsInstance(record["duration_ms"], int)
            self.assertTrue(record["request_id"])
        self.assertNotEqual(first["request_id"], second["request_id"])

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_retrieval_includes_history_for_follow_ups(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        chunks = [{"source": "set-up-cursor -- https://example.com", "text": "mcp.json"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.75)
        mock_answer.return_value = {"answer": "Use ~/.cursor/mcp.json.", "citations": []}
        history = [{"question": "how do I set up Cursor?", "answer": "Use MCP."}]

        ask("where does the config file go?", history=history)

        query = mock_retrieve_scored.call_args[0][0]
        self.assertIn("Cursor", query)
        self.assertIn("where does the config file go?", query)

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_refuses_when_retrieval_empty(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        mock_retrieve_scored.return_value = RetrievalResult(chunks=[], top_score=None)

        result = ask("what is the meaning of life?")

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        self.assertEqual(result["citations"], [])
        mock_answer.assert_not_called()
        _mock_log.assert_called_once()

    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_refuses_blank_question(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock
    ) -> None:
        result = ask("   ")

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        self.assertEqual(result["citations"], [])
        mock_retrieve_scored.assert_not_called()
        mock_answer.assert_not_called()

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_run_question_skips_answer_when_no_chunks(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        mock_retrieve_scored.return_value = RetrievalResult(chunks=[], top_score=None)

        result = run_question("unknown topic", verbose=False)

        self.assertEqual(result["answer"], REFUSAL_PHRASE)
        mock_answer.assert_not_called()
        _mock_log.assert_called_once()

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

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_caches_repeat_retrieval_queries(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.82)
        mock_answer.return_value = {
            "answer": "Run ark host enroll.",
            "citations": ["getting-started -- https://example.com"],
        }

        ask("how do I enroll a host?")
        ask("how do i enroll a host?")

        mock_retrieve_scored.assert_called_once()
        mock_answer.assert_called_once()

    def test_normalize_cache_key_strips_trailing_punctuation(self) -> None:
        self.assertEqual(
            _normalize_cache_key("how do i enroll a host?"),
            _normalize_cache_key("how do i enroll a host"),
        )

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_answer_cache_hits_repeat_even_with_history(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.82)
        mock_answer.return_value = {
            "answer": "Run ark host enroll.",
            "citations": ["getting-started -- https://example.com"],
        }
        history = [{"question": "how do I enroll a host?", "answer": "Run ark host enroll."}]

        ask("how do I enroll a host?")
        ask("how do I enroll a host?", history=history)

        mock_retrieve_scored.assert_called_once()
        mock_answer.assert_called_once()

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_answer_cache_does_not_store_follow_up_answers(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        chunks = [{"source": "set-up-cursor -- https://example.com", "text": "mcp.json"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.75)
        mock_answer.return_value = {"answer": "Use ~/.cursor/mcp.json.", "citations": []}
        history = [{"question": "how do I set up Cursor?", "answer": "Use MCP."}]

        ask("where does the config file go?", history=history)
        ask("where does the config file go?", history=history)

        mock_retrieve_scored.assert_called_once()
        self.assertEqual(mock_answer.call_count, 2)

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_answer_cache_can_be_disabled(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        os.environ["ASK_ANSWER_CACHE"] = "0"
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.82)
        mock_answer.return_value = {"answer": "Run ark host enroll.", "citations": []}

        ask("how do I enroll a host?")
        ask("how do I enroll a host?")

        self.assertEqual(mock_answer.call_count, 2)

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_cache_misses_when_history_differs(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        chunks = [{"source": "set-up-cursor -- https://example.com", "text": "mcp.json"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.75)
        mock_answer.return_value = {"answer": "Use ~/.cursor/mcp.json.", "citations": []}
        history = [{"question": "how do I set up Cursor?", "answer": "Use MCP."}]

        ask("where does the config file go?", history=history)
        ask("where does the config file go?")

        self.assertEqual(mock_retrieve_scored.call_count, 2)

    @patch("src.ask.log_query")
    @patch("src.ask.answer")
    @patch("src.ask.retrieve_scored")
    def test_ask_cache_can_be_disabled(
        self, mock_retrieve_scored: MagicMock, mock_answer: MagicMock, _mock_log: MagicMock
    ) -> None:
        os.environ["ASK_RETRIEVAL_CACHE"] = "0"
        os.environ["ASK_ANSWER_CACHE"] = "0"
        chunks = [{"source": "getting-started -- https://example.com", "text": "enroll host"}]
        mock_retrieve_scored.return_value = RetrievalResult(chunks=chunks, top_score=0.82)
        mock_answer.return_value = {"answer": "Run ark host enroll.", "citations": []}

        ask("how do I enroll a host?")
        ask("how do I enroll a host?")

        self.assertEqual(mock_retrieve_scored.call_count, 2)



class ObservabilityNeverFailsTheAnswerTests(unittest.TestCase):
    @patch("src.ask.log_query", side_effect=OSError("read-only file system"))
    def test_a_failed_query_log_write_still_returns_the_answer(self, _log) -> None:
        from src.ask import _log_ask_result
        # Must not raise: the answer has already been generated by this point.
        _log_ask_result("q?", {"answer": "Run ark host enroll."}, channel="web", session_id=None)

if __name__ == "__main__":
    unittest.main()
