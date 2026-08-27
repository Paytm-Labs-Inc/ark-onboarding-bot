"""Guardrails for the eval gold set."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "eval" / "questions.json"
MIN_GOLD_QUESTIONS = 44
MIN_SCORED_QUESTIONS = 32
MIN_REFUSAL_QUESTIONS = 11


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



class MultiSourceLabelGuardrailTests(unittest.TestCase):
    """A label may name several pages only when each of them carries the answer.

    Multi-page labels make the page-level gate easier to satisfy, so every page
    listed has to earn its place: it must contain every answer marker.
    """

    def test_every_listed_page_carries_every_marker(self) -> None:
        import json
        from pathlib import Path

        questions = json.loads(Path("eval/questions.json").read_text(encoding="utf-8"))
        for item in questions:
            expected = item.get("expected_source")
            if not isinstance(expected, list):
                continue
            markers = [str(m).lower() for m in item.get("answer_must_include") or []]
            self.assertTrue(markers, f"{item['id']}: multi-page label needs answer markers")
            for page in expected:
                text = Path("data", f"{page}.md").read_text(encoding="utf-8").lower()
                missing = [m for m in markers if m not in text]
                self.assertEqual(missing, [], f"{item['id']}: {page} lacks {missing}")

if __name__ == "__main__":
    unittest.main()
