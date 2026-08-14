"""Guardrails for the eval gold set."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "eval" / "questions.json"
MIN_GOLD_QUESTIONS = 30
MIN_SCORED_QUESTIONS = 24
MIN_REFUSAL_QUESTIONS = 6


class EvalGoldSetTests(unittest.TestCase):
    def test_gold_set_size(self) -> None:
        questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
        scored = [item for item in questions if item.get("expected_source") is not None]
        refusals = [item for item in questions if item.get("expect_refusal")]

        self.assertGreaterEqual(len(questions), MIN_GOLD_QUESTIONS)
        self.assertGreaterEqual(len(scored), MIN_SCORED_QUESTIONS)
        self.assertGreaterEqual(len(refusals), MIN_REFUSAL_QUESTIONS)

    def test_question_ids_unique(self) -> None:
        questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
        ids = [str(item["id"]) for item in questions]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
