"""Tests for eval runner helpers."""

from __future__ import annotations

import unittest

from eval.run_eval import filter_questions, main


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


if __name__ == "__main__":
    unittest.main()
