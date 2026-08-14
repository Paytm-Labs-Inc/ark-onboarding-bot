"""Tests for citation parsing."""

from __future__ import annotations

import unittest

from src.citations import parse_citation


class CitationParseTests(unittest.TestCase):
    def test_splits_slug_and_url(self) -> None:
        parsed = parse_citation(
            "set-up-cursor -- https://foundry.mypaytm.com/onboarding/cursor"
        )
        self.assertEqual(parsed["slug"], "set-up-cursor")
        self.assertIn("foundry.mypaytm.com", parsed["url"])

    def test_handles_plain_label(self) -> None:
        parsed = parse_citation("faq")
        self.assertEqual(parsed["slug"], "faq")
        self.assertEqual(parsed["url"], "")


if __name__ == "__main__":
    unittest.main()
