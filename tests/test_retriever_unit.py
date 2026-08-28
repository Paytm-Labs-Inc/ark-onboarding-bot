"""Unit tests for the retriever (embedding is mocked)."""

from __future__ import annotations

import os
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
                "text": "## Start here\nFour steps stand between a new account and a session",
            },
            {
                "source": "getting-access -- https://example.com/access",
                "text": "**Step 1** - open the onboarding tab",
            },
        ]
        index = build_index(chunks)
        results = retrieve("what are the steps to onboard ark", top_k=3, index=index)
        texts = [chunk["text"] for chunk in results]
        self.assertTrue(any("Start here" in text for text in texts))

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

    @patch("src.retriever._embed", side_effect=_fake_embed)
    def test_jira_mcp_vpn_pins_faq_chunk(self, _mock) -> None:
        chunks = CHUNKS + [
            {
                "source": "faq-google-doc -- https://example.com/faq-doc",
                "text": (
                    '12.4 "Jira / Bitbucket MCP is blocked from Ark — '
                    'pods are not on VPN. How to fix?" NAT gateway.'
                ),
            },
            {
                "source": "getting-access -- https://example.com/access",
                "text": "You must be on the corp network/VPN to open Ark.",
            },
            {
                "source": "set-up-cursor -- https://example.com/cursor",
                "text": "VPN required. The control plane is reachable only from VPN.",
            },
        ]
        index = build_index(chunks)
        results = retrieve(
            "jira mcp is blocked from ark pods not on vpn how to fix",
            top_k=3,
            index=index,
        )
        texts = " ".join(chunk["text"] for chunk in results)
        self.assertIn("Jira / Bitbucket MCP is blocked from Ark", texts)
        self.assertTrue(
            any("faq-google-doc" in chunk["source"] for chunk in results)
        )




class PinnedMarkersMatchCorpusTests(unittest.TestCase):
    """Every pinned marker must match at least one chunk of the real corpus.

    A marker that matches nothing is a rule that silently stopped working.
    Six of them had, after the #23 ingest rewrote the pages they were copied
    from, and nothing noticed because a dead marker fails quietly.
    """

    def test_every_marker_matches_a_chunk(self) -> None:
        from src.chunker import DATA_DIR, load_chunks

        chunks = load_chunks(DATA_DIR)
        dead: list[str] = []
        for name in dir(retriever):
            if not name.endswith("PINNED_MARKERS"):
                continue
            for marker in getattr(retriever, name):
                if not any(marker in chunk["text"] for chunk in chunks):
                    dead.append(f"{name}: {marker!r}")
        self.assertEqual(dead, [], "pinned markers that match no chunk")


class HybridRetrievalTests(unittest.TestCase):
    CHUNKS = [
        {"source": "setup -- u", "text": "General setup notes about editors and machines."},
        {"source": "notes-a -- u", "text": "More notes on editors."},
        {"source": "notes-b -- u", "text": "Even more notes on machines."},
        {"source": "cursor -- u", "text": "Put the server block in .cursor/mcp.json and restart Cursor."},
    ]
    # Dense alone ranks the identifier chunk LAST for this query; with dense
    # voting 2:1 the lexical list can lift it, but only past the weakest dense
    # neighbour -- which is the real claim, so that is what the test asserts.
    VECTORS = {"General": [1.0, 0.0], "More": [0.9, 0.1], "Even": [0.8, 0.2], "Put": [0.0, 1.0]}

    @classmethod
    def _embed(cls, texts):
        import numpy as np
        rows = [cls.VECTORS.get(t.split()[0], [1.0, 0.0]) for t in texts]  # query -> [1, 0]
        return np.asarray(rows, dtype=np.float32)

    def test_tokens_keep_identifiers_whole_and_split(self) -> None:
        from src.retriever import _tokens
        toks = _tokens("Edit .cursor/mcp.json then run ark host enroll")
        self.assertIn("cursor/mcp.json", toks)
        self.assertIn("mcp.json", toks)  # last path segment, whole
        self.assertIn("json", toks)
        self.assertIn("enroll", toks)

    def test_bm25_ranks_the_exact_identifier_first(self) -> None:
        from src.retriever import _BM25, _tokens
        bm = _BM25([_tokens(c["text"]) for c in self.CHUNKS])
        self.assertEqual(int(bm.scores(_tokens("mcp.json")).argmax()), 3)

    def test_rrf_sums_reciprocal_ranks(self) -> None:
        import numpy as np
        from src.retriever import _rrf
        fused = _rrf(np.array([0, 1]), np.array([1, 0]), k=1)
        self.assertAlmostEqual(float(fused[0]), 1 / 2 + 1 / 3)
        self.assertAlmostEqual(float(fused[1]), 1 / 3 + 1 / 2)

    def test_hybrid_lifts_the_identifier_chunk_dense_alone_buries(self) -> None:
        def rank_of_identifier(results):
            return next(i for i, c in enumerate(results) if "mcp.json" in c["text"])

        with patch("src.retriever._embed", side_effect=self._embed):
            index = build_index(self.CHUNKS)
            with patch.dict(os.environ, {"ASK_HYBRID": "0"}):
                dense_only = retrieve("where is the mcp.json file", top_k=4, index=index, use_pins=False)
            with patch.dict(os.environ, {"ASK_HYBRID": "1"}):
                hybrid = retrieve("where is the mcp.json file", top_k=4, index=index, use_pins=False)
        self.assertEqual(rank_of_identifier(dense_only), 3)  # buried last by dense
        self.assertLess(rank_of_identifier(hybrid), 3)

    def test_flag_parses(self) -> None:
        from src.retriever import hybrid_enabled
        with patch.dict(os.environ, {"ASK_HYBRID": "0"}):
            self.assertFalse(hybrid_enabled())
        with patch.dict(os.environ, {"ASK_HYBRID": ""}):
            self.assertTrue(hybrid_enabled())

if __name__ == "__main__":
    unittest.main()
