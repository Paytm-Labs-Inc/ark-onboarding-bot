"""Tests for query log summarization."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eval.summarize_queries import load_records, summarize


class SummarizeQueriesTests(unittest.TestCase):
    def test_summarize_counts_volume_and_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "query_log.jsonl"
            rows = [
                {
                    "ts": "2026-08-17T10:00:00+00:00",
                    "question": "how do I enroll a host?",
                    "refused": False,
                    "low_confidence": False,
                    "gold_set_candidate": False,
                    "top_score": 0.82,
                    "chunk_count": 5,
                },
                {
                    "ts": "2026-08-17T11:00:00+00:00",
                    "question": "what is the weather?",
                    "refused": True,
                    "low_confidence": False,
                    "gold_set_candidate": True,
                    "top_score": None,
                    "chunk_count": 0,
                },
                {
                    "ts": "2026-08-17T12:00:00+00:00",
                    "question": "how do i set up curser",
                    "refused": False,
                    "low_confidence": True,
                    "gold_set_candidate": True,
                    "top_score": 0.21,
                    "chunk_count": 4,
                },
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            summary = summarize(load_records(path))

        self.assertEqual(summary["total_queries"], 3)
        self.assertEqual(summary["refused"], 1)
        self.assertEqual(summary["low_confidence"], 1)
        self.assertEqual(len(summary["gold_set_candidates"]), 2)
        self.assertEqual(summary["queries_by_day"]["2026-08-17"], 3)
        self.assertAlmostEqual(summary["retrieval_hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
