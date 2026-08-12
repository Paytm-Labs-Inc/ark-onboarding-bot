"""Unit tests for the retriever (embedding is mocked)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from src import retriever
from src.retriever import build_index, retrieve

_VOCAB = ("enroll", "cursor", "jira")


def _fake_embed(texts: list[str]) -> np.ndarray:
    rows = []
    for text in texts:
        vec = np.zeros(len(_VOCAB), dtype=np.float32)
        for i, word in enumerate(_VOCAB):
            if word in text.lower():
                vec[i] = 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        rows.append(vec)
    return np.asarray(rows, dtype=np.float32)


CHUNKS = [
    {"source": "getting-started -- u1", "text": "how to enroll a host"},
    {"source": "set-up-cursor -- u2", "text": "set up cursor mcp"},
    {"source": "faq -- u3", "text": "reset jira password"},
]


class RetrieverTests(unittest.TestCase):
    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_returns_dicts_and_respects_top_k(self, _mock) -> None:
        index = build_index(CHUNKS)
        results = retrieve("how do I enroll a host", top_k=2, index=index)
        self.assertEqual(len(results), 2)
        for chunk in results:
            self.assertIn("source", chunk)
            self.assertIn("text", chunk)
        self.assertEqual(results[0]["text"], "how to enroll a host")

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_top_k_larger_than_corpus_returns_all(self, _mock) -> None:
        index = build_index(CHUNKS)
        results = retrieve("enroll cursor jira", top_k=99, index=index)
        self.assertEqual(len(results), 3)

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_empty_question_returns_empty(self, _mock) -> None:
        index = build_index(CHUNKS)
        self.assertEqual(retrieve("   ", top_k=5, index=index), [])

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_empty_index_returns_empty(self, _mock) -> None:
        index = build_index([])
        self.assertEqual(retrieve("anything", top_k=5, index=index), [])


if __name__ == "__main__":
    unittest.main()
