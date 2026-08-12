"""Unit tests for keyword retrieval fallback."""

from __future__ import annotations

import unittest

from src.retrieve_stub import retrieve


class RetrieveStubTests(unittest.TestCase):
    def test_retrieve_finds_host_enroll_chunk(self) -> None:
        chunks = retrieve("how do I enroll a host?", k=3)
        self.assertTrue(chunks)
        joined = " ".join(chunk["text"].lower() for chunk in chunks)
        self.assertIn("enroll", joined)

    def test_retrieve_returns_empty_for_unrelated_question(self) -> None:
        chunks = retrieve("xyzzy plugh qwerty zzztop", k=5)
        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
