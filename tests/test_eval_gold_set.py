"""Guardrails for the eval gold set."""

from __future__ import annotations

import itertools
import json
import re
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


class DuplicatePageLabelGuardTests(unittest.TestCase):
    """A page ingested twice must be labelled as one source, or not at all.

    The FAQ is ingested from the site scrape (`faq`) and from the Google Doc
    (`faq-google-doc`). A question either copy answers can be cited as either
    id, depending only on which half retrieval ranks first, so a row naming one
    of them fails a *correct* citation at random. That is what took `full-eval`
    red on four consecutive runs before #61, each time on a different question.

    Measured rather than hard-coded, so this keeps working when the corpus
    dedupe lands (no pairs left, passes trivially) or a new duplicate appears.
    """

    ROOT = Path(__file__).resolve().parents[1]
    SHINGLE = 5
    # `faq` <-> `faq-google-doc` overlap 0.96; the next closest pair in the
    # corpus is 0.017. Anything between the two separates a re-ingest of the
    # same document from two pages that merely cover related ground.
    THRESHOLD = 0.5

    @classmethod
    def _shingles(cls, text: str) -> set[tuple[str, ...]]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        n = cls.SHINGLE
        return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}

    def _duplicate_pairs(self) -> list[tuple[str, str]]:
        pages = {
            path.stem: self._shingles(path.read_text(encoding="utf-8"))
            for path in sorted((self.ROOT / "data").glob("*.md"))
        }
        pairs = []
        for a, b in itertools.combinations(sorted(pages), 2):
            first, second = pages[a], pages[b]
            if not first or not second:
                continue
            if len(first & second) / len(first | second) >= self.THRESHOLD:
                pairs.append((a, b))
        return pairs

    def test_a_duplicated_page_is_never_labelled_by_only_one_of_its_ids(self) -> None:
        duplicates = self._duplicate_pairs()
        questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))

        for item in questions:
            expected = item.get("expected_source")
            if expected is None:
                continue
            accepted = set(expected if isinstance(expected, list) else [expected])
            for a, b in duplicates:
                named = accepted & {a, b}
                self.assertNotEqual(
                    len(named),
                    1,
                    f"{item['id']}: {a!r} and {b!r} are the same document "
                    f"(>={self.THRESHOLD:.0%} overlap), so a correct answer may cite "
                    f"either one, but the label names only {sorted(named)}. Add the "
                    f"other page and its page_evidence quote.",
                )


if __name__ == "__main__":
    unittest.main()
