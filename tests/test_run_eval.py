"""Tests for eval runner helpers."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from eval.run_eval import (
    QuestionResult,
    chunk_metrics,
    detail_status,
    evaluate_question,
    expected_sources,
    filter_questions,
    first_relevant_rank,
    full_eval_ready,
    main,
    print_report,
    roadmap_promise_unbacked,
    random_baseline,
)


class RunEvalFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questions = [
            {"id": "scored", "expected_source": "faq", "expect_refusal": False},
            {"id": "refusal", "expected_source": None, "expect_refusal": True},
        ]

    def test_filter_only_refusals(self) -> None:
        filtered = filter_questions(
            self.questions, only_refusals=True, only_scored=False
        )
        self.assertEqual([item["id"] for item in filtered], ["refusal"])

    def test_filter_only_scored(self) -> None:
        filtered = filter_questions(
            self.questions, only_refusals=False, only_scored=True
        )
        self.assertEqual([item["id"] for item in filtered], ["scored"])

    def test_only_refusals_requires_full(self) -> None:
        code = main(["--only-refusals", "--quiet-retriever"])
        self.assertEqual(code, 2)

    @patch("eval.run_eval.load_dotenv_for_full")
    def test_full_without_pi_key_exits_2(self, _load: object) -> None:
        saved_pi = os.environ.pop("PI_API_KEY", None)
        saved_backend = os.environ.pop("ANSWER_BACKEND", None)
        try:
            code = main(["--full", "--quiet-retriever", "--only-scored"])
            self.assertEqual(code, 2)
        finally:
            if saved_pi is not None:
                os.environ["PI_API_KEY"] = saved_pi
            if saved_backend is not None:
                os.environ["ANSWER_BACKEND"] = saved_backend

    def test_full_eval_ready_asks_for_pi_key(self) -> None:
        saved_pi = os.environ.pop("PI_API_KEY", None)
        saved_backend = os.environ.pop("ANSWER_BACKEND", None)
        try:
            message = full_eval_ready()
            self.assertIsNotNone(message)
            assert message is not None
            self.assertIn("PI_API_KEY", message)
        finally:
            if saved_pi is not None:
                os.environ["PI_API_KEY"] = saved_pi
            if saved_backend is not None:
                os.environ["ANSWER_BACKEND"] = saved_backend

    def test_detail_status_flags_citation_miss(self) -> None:
        result = QuestionResult(
            id="ssh-clone",
            question="clone failed",
            expected_source="faq",
            expect_refusal=False,
            retrieval_hit=True,
            retrieved_sources=["faq -- https://x"],
            citation_hit=False,
            citations=["first-run -- https://y"],
            answer_hit=True,
            answer_preview="use the key",
        )
        self.assertEqual(detail_status(result, run_answer=True), "CITATION_MISS")

    def test_print_report_lists_citation_miss_ids(self) -> None:
        result = QuestionResult(
            id="ssh-clone",
            question="clone failed",
            expected_source="faq",
            expect_refusal=False,
            retrieval_hit=True,
            retrieved_sources=["faq -- https://x"],
            citation_hit=False,
            citations=["first-run -- https://y"],
            answer_hit=True,
            answer_preview="use the key",
        )
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            print_report(
                [result],
                run_answer=True,
                top_k=8,
                max_chars=2000,
                model_name="all-MiniLM-L6-v2",
            )
        output = buffer.getvalue()
        self.assertIn("Citation misses", output)
        self.assertIn("ssh-clone", output)
        self.assertIn("[CITATION_MISS]", output)



class ChunkLevelMetricsTests(unittest.TestCase):
    """Chunk-level rank uses the answer markers as the relevance label."""

    def _result(self, rank, markers=("mcp.json",)):
        return QuestionResult(
            id="q", question="q", expected_source="x", expect_refusal=False,
            retrieval_hit=True, retrieved_sources=[], citation_hit=None,
            citations=[], answer_hit=None, answer_preview=None,
            chunk_rank=rank, answer_markers=list(markers),
        )

    def test_first_relevant_rank_is_one_based_and_case_insensitive(self) -> None:
        chunks = [{"text": "nothing here"}, {"text": "Put it in MCP.JSON"}, {"text": "mcp.json"}]
        self.assertEqual(first_relevant_rank(chunks, ["mcp.json"]), 2)
        self.assertIsNone(first_relevant_rank(chunks, ["absent"]))

    def test_chunk_metrics_recall_and_mrr(self) -> None:
        scored = [self._result(1), self._result(4), self._result(None), self._result(None, markers=())]
        hits, labelled, mrr = chunk_metrics(scored, top_k=3)
        self.assertEqual((hits, labelled), (1, 3))          # rank 4 is outside k=3
        self.assertAlmostEqual(mrr, (1.0 + 0.25 + 0.0) / 3)  # unlabelled row excluded

    @patch("src.retrieve.retrieve")
    def test_evaluate_question_records_chunk_rank(self, mock_retrieve) -> None:
        mock_retrieve.return_value = [
            {"source": "set-up-cursor -- u", "text": "intro"},
            {"source": "set-up-cursor -- u", "text": "add it to .cursor/mcp.json"},
        ]
        item = {"id": "c", "question": "where?", "expected_source": "set-up-cursor",
                "answer_must_include": ["mcp.json"]}
        result = evaluate_question(item, top_k=8, run_answer=False, use_pins=False)
        self.assertEqual(result.chunk_rank, 2)
        self.assertEqual(result.answer_markers, ["mcp.json"])
        mock_retrieve.assert_called_once_with("where?", k=8, use_pins=False)


class NoPinsGuardTests(unittest.TestCase):
    def test_no_pins_refuses_full(self) -> None:
        err = io.StringIO()
        with patch("sys.stderr", err):
            code = main(["--full", "--no-pins", "--only-scored"])
        self.assertEqual(code, 2)
        self.assertIn("retrieval-only", err.getvalue())

    @patch("src.retrieve.retrieve", side_effect=RuntimeError("index down"))
    def test_error_path_keeps_markers_so_it_counts_as_a_miss(self, _r) -> None:
        item = {"id": "q", "question": "q?", "expected_source": "x", "answer_must_include": ["m"]}
        result = evaluate_question(item, top_k=8, run_answer=False)
        self.assertEqual(result.answer_markers, ["m"])
        self.assertIsNone(result.chunk_rank)
        hits, labelled, _ = chunk_metrics([result], top_k=8)
        self.assertEqual((hits, labelled), (0, 1))


class RoadmapPromiseEvalTests(unittest.TestCase):
    @patch("src.retrieve.retrieve", return_value=[{"source": "faq -- u", "text": "x"}])
    @patch("src.ask.ask")
    def test_full_eval_marks_an_embedded_unbacked_promise_wrong(self, mock_ask, _r) -> None:
        from src.answer import ROADMAP_PHRASE
        mock_ask.return_value = {"answer": "Use the CLI. " + ROADMAP_PHRASE, "citations": ["faq -- u"]}
        item = {"id": "q", "question": "q?", "expected_source": "faq", "answer_must_include": ["CLI"]}
        result = evaluate_question(item, top_k=8, run_answer=True)
        self.assertFalse(result.answer_hit)  # marker present, promise unbacked -> wrong

    def test_promise_without_roadmap_citation_is_unbacked(self) -> None:
        from src.answer import ROADMAP_PHRASE
        self.assertTrue(roadmap_promise_unbacked(ROADMAP_PHRASE, ["faq -- https://x"]))
        self.assertFalse(roadmap_promise_unbacked(ROADMAP_PHRASE, ["roadmap -- https://x"]))
        self.assertFalse(roadmap_promise_unbacked("Run ark host enroll.", []))
class MultiSourceLabelTests(unittest.TestCase):
    def test_expected_sources_accepts_string_or_list(self) -> None:
        self.assertEqual(expected_sources({"expected_source": "faq"}), ["faq"])
        self.assertEqual(expected_sources({"expected_source": ["a", "b"]}), ["a", "b"])
        self.assertEqual(expected_sources({}), [])

    @patch("src.retrieve.retrieve")
    def test_any_accepted_page_is_a_hit(self, mock_retrieve) -> None:
        mock_retrieve.return_value = [{"source": "first-run -- u", "text": "x"}]
        item = {"id": "q", "question": "q?", "expected_source": ["getting-started", "first-run"]}
        result = evaluate_question(item, top_k=8, run_answer=False)
        self.assertTrue(result.retrieval_hit)
        self.assertEqual(result.expected_source, "getting-started|first-run")


class MultiSourceCitationTests(unittest.TestCase):
    @patch("src.retrieve.retrieve", return_value=[{"source": "first-run -- u", "text": "x"}])
    @patch("src.ask.ask")
    def test_citation_of_any_listed_page_is_a_hit(self, mock_ask, _r) -> None:
        item = {"id": "q", "question": "q?", "expected_source": ["getting-started", "first-run"],
                "answer_must_include": ["nouns"]}
        mock_ask.return_value = {"answer": "Four nouns.", "citations": ["first-run -- u"]}
        self.assertTrue(evaluate_question(item, top_k=8, run_answer=True).citation_hit)
        mock_ask.return_value = {"answer": "Four nouns.", "citations": ["roadmap -- u"]}
        self.assertFalse(evaluate_question(item, top_k=8, run_answer=True).citation_hit)


class MultiSourceNegativeTests(unittest.TestCase):
    @patch("src.retrieve.retrieve")
    def test_no_listed_page_is_a_miss(self, mock_retrieve) -> None:
        mock_retrieve.return_value = [{"source": "roadmap -- u", "text": "x"}]
        item = {"id": "q", "question": "q?", "expected_source": ["getting-started", "first-run"]}
        self.assertFalse(evaluate_question(item, top_k=8, run_answer=False).retrieval_hit)

    def test_random_baseline_reads_joined_labels(self) -> None:
        r = QuestionResult(id="q", question="q", expected_source="getting-started|first-run",
                           expect_refusal=False, retrieval_hit=True, retrieved_sources=[],
                           citation_hit=None, citations=[], answer_hit=None, answer_preview=None)
        with patch("src.chunker.load_chunks", return_value=[{"source": "first-run -- u", "text": "x"}]):
            self.assertEqual(random_baseline([r], top_k=1, trials=3), 100.0)

if __name__ == "__main__":
    unittest.main()
