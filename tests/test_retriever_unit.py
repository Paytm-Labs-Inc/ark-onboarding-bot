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

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_onboarding_steps_pins_checklist_chunk(self, _mock) -> None:
        chunks = CHUNKS + [
            {
                "source": "getting-started -- https://example.com",
                "text": "## Onboarding path\n1. Getting access\n2. Secrets",
            },
            {
                "source": "getting-access -- https://example.com/access",
                "text": "**Step 1** - open the onboarding tab",
            },
        ]
        index = build_index(chunks)
        results = retrieve("what are the steps to onboard ark", top_k=3, index=index)
        texts = [chunk["text"] for chunk in results]
        self.assertTrue(any("Onboarding path" in text for text in texts))

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_enroll_host_pins_faq_chunk(self, _mock) -> None:
        chunks = CHUNKS + [
            {
                "source": "faq -- https://example.com/faq",
                "text": "Run `ark host enroll` with your user API key.",
            },
            {
                "source": "getting-started -- https://example.com",
                "text": "Registering a machine as compute is an admin action.",
            },
        ]
        index = build_index(chunks)
        results = retrieve("how to enroll a host", top_k=3, index=index)
        texts = " ".join(chunk["text"] for chunk in results)
        self.assertIn("ark host enroll", texts)

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_workspace_steps_pins_apply_chunk(self, _mock) -> None:
        chunks = CHUNKS + [
            {
                "source": "getting-access -- https://example.com/access",
                "text": "**Step 2 - Wire the workspace.** Run ark workspace apply myteam-workspace.yaml",
            },
        ]
        index = build_index(chunks)
        results = retrieve("what are the steps to create a workspace", top_k=3, index=index)
        texts = " ".join(chunk["text"] for chunk in results)
        self.assertIn("ark workspace apply", texts)

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_workspace_ownership_pins_apply_chunk(self, _mock) -> None:
        chunks = CHUNKS + [
            {
                "source": "getting-access -- https://example.com/access",
                "text": (
                    "ark workspace apply myteam-workspace.yaml. "
                    "start my myteam-review flow on the myteam-workspace workspace"
                ),
            },
            {
                "source": "faq -- https://example.com/faq",
                "text": "Workspace not found. Wrong tenant/team, or not created.",
            },
        ]
        index = build_index(chunks)
        results = retrieve(
            "can I use someone else's workspace or create my own",
            top_k=3,
            index=index,
        )
        texts = " ".join(chunk["text"] for chunk in results)
        self.assertIn("ark workspace apply", texts)

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_cursor_access_pins_setup_chunk(self, _mock) -> None:
        chunks = CHUNKS + [
            {
                "source": "set-up-cursor -- https://example.com/cursor",
                "text": (
                    "# Set up Cursor\nAdd the ark MCP server to Cursor. "
                    "Use ~/.cursor/mcp.json globally."
                ),
            },
            {
                "source": "faq -- https://example.com/faq",
                "text": '7.4 "Can we use Cursor?" In progress, not yet.',
            },
        ]
        index = build_index(chunks)
        results = retrieve("how to access cursor?", top_k=3, index=index)
        texts = " ".join(chunk["text"] for chunk in results)
        self.assertIn("Set up Cursor", texts)
        self.assertIn(".cursor/mcp.json", texts)


if __name__ == "__main__":
    unittest.main()
