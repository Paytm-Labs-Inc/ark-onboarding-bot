"""Tests for eval runner helpers."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from eval.run_eval import (
    QuestionResult,
    detail_status,
    filter_questions,
    full_eval_ready,
    main,
    print_report,
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


if __name__ == "__main__":
    unittest.main()
