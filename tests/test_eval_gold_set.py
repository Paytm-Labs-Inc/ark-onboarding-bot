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
    """A label may name several pages only with evidence that each one answers.

    The shared answer markers are too generic to guard this ("workspace" and
    "flow" appear on 14 of 17 pages), so a multi-page label carries
    page_evidence: one quote per listed page that must occur on that page.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_every_listed_page_has_its_own_evidence_quote(self) -> None:
        import json

        questions = json.loads((self.ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
        for item in questions:
            expected = item.get("expected_source")
            if not isinstance(expected, list):
                self.assertNotIn("page_evidence", item, f"{item['id']}: evidence without a multi-page label")
                continue
            evidence = item.get("page_evidence") or {}
            self.assertEqual(sorted(evidence), sorted(expected), f"{item['id']}: page_evidence must cover exactly the listed pages")
            for page, quote in evidence.items():
                text = (self.ROOT / "data" / f"{page}.md").read_text(encoding="utf-8").lower()
                self.assertIn(str(quote).lower(), text, f"{item['id']}: {page!r} does not contain {quote!r}")


if __name__ == "__main__":
    unittest.main()
