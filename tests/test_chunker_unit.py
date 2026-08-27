"""Unit tests for the corpus chunker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.chunker import MAX_CHARS, OVERLAP_CHARS, _split_fixed, load_chunks


class ChunkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, text: str) -> None:
        (self.data_dir / name).write_text(text, encoding="utf-8")

    def test_sections_become_chunks_with_labels(self) -> None:
        self._write(
            "getting-started.md",
            "Source: https://foundry.mypaytm.com/onboarding/\n\n"
            "# Onboarding\nintro para\n"
            "## Section A\nbody a\n"
            "## Section B\nbody b\n",
        )
        chunks = load_chunks(self.data_dir)
        self.assertEqual(len(chunks), 3)  # preamble + 2 sections
        for chunk in chunks:
            self.assertIn("source", chunk)
            self.assertIn("text", chunk)
            self.assertEqual(
                chunk["source"],
                "getting-started -- https://foundry.mypaytm.com/onboarding/",
            )
        self.assertNotIn("\u2014", chunks[0]["source"])  # no em-dash (per §IV.21)

    def test_file_without_h2_yields_one_chunk(self) -> None:
        self._write(
            "faq-google-doc.md",
            "Source: https://docs.google.com/document/d/x/edit\n\n"
            "plain body with no markdown headers at all",
        )
        chunks = load_chunks(self.data_dir)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "plain body with no markdown headers at all")

    def test_oversized_section_is_split_with_fallback(self) -> None:
        big = "x " * MAX_CHARS  # ~2*MAX_CHARS chars, no h2
        self._write("faq.md", f"Source: https://foundry.mypaytm.com/faq\n\n{big}")
        chunks = load_chunks(self.data_dir)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), MAX_CHARS)

    def test_missing_source_header_raises(self) -> None:
        self._write("bad.md", "no header here\n\nbody")
        with self.assertRaises(RuntimeError):
            load_chunks(self.data_dir)

    def test_empty_corpus_dir_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            load_chunks(self.data_dir)

    def test_split_fixed_rejects_overlap_ge_max_chars(self) -> None:
        with self.assertRaises(ValueError):
            _split_fixed("abcdefghij", max_chars=OVERLAP_CHARS, overlap=OVERLAP_CHARS)

    def test_split_fixed_advances_when_overlap_lt_max_chars(self) -> None:
        text = "a" * (MAX_CHARS + 50)
        pieces = _split_fixed(text, MAX_CHARS, OVERLAP_CHARS)
        self.assertGreater(len(pieces), 1)
        joined = pieces[0][-OVERLAP_CHARS:]
        self.assertTrue(any(piece.startswith(joined) for piece in pieces[1:]))



class SplitSectionKeepsHeadingTests(unittest.TestCase):
    def test_every_piece_of_a_split_section_starts_with_its_heading(self) -> None:
        from src import chunker
        with tempfile.TemporaryDirectory() as tmp:
            body = "## Start here\n\n" + ("step text " * 400)  # well over MAX_CHARS
            Path(tmp, "page.md").write_text("Source: https://x/page\n" + body, encoding="utf-8")
            chunks = chunker.load_chunks(Path(tmp))
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(chunk["text"].startswith("## Start here"), chunk["text"][:40])

    def test_the_real_start_here_section_is_pinned_on_every_piece(self) -> None:
        from src.chunker import DATA_DIR, load_chunks
        pieces = [c for c in load_chunks(DATA_DIR) if c["text"].startswith("## Start here")]
        self.assertGreater(len(pieces), 1, "section no longer splits; test needs updating")

if __name__ == "__main__":
    unittest.main()
