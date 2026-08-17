"""Tests for query observability logging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.answer import REFUSAL_PHRASE
from src import query_log as query_log_module


class QueryLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "query_log.jsonl"
        self.log_patch = patch.object(query_log_module, "QUERY_LOG_PATH", self.log_path)
        self.log_patch.start()

    def tearDown(self) -> None:
        self.log_patch.stop()
        self.temp_dir.cleanup()

    def test_build_record_flags_refusal(self) -> None:
        record = query_log_module.build_record(
            question="what is the weather?",
            answer=REFUSAL_PHRASE,
            citations=[],
            retrieved_sources=[],
            top_score=None,
            chunk_count=0,
            channel="web",
            session_id="sess-1",
        )
        self.assertTrue(record["refused"])
        self.assertFalse(record["low_confidence"])
        self.assertTrue(record["gold_set_candidate"])

    def test_build_record_flags_low_confidence(self) -> None:
        record = query_log_module.build_record(
            question="how do i set up curser",
            answer="Use MCP.",
            citations=["set-up-cursor -- https://example.com"],
            retrieved_sources=["set-up-cursor -- https://example.com"],
            top_score=0.21,
            chunk_count=3,
            channel="cli",
            session_id=None,
        )
        self.assertFalse(record["refused"])
        self.assertTrue(record["low_confidence"])
        self.assertTrue(record["gold_set_candidate"])

    def test_log_query_appends_jsonl(self) -> None:
        query_log_module.log_query(
            question="how do I enroll a host?",
            answer="Run ark host enroll.",
            citations=["getting-started -- https://example.com"],
            retrieved_sources=["getting-started -- https://example.com"],
            top_score=0.88,
            chunk_count=4,
            channel="web",
            session_id="sess-2",
        )
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["question"], "how do I enroll a host?")
        self.assertEqual(payload["channel"], "web")
        self.assertFalse(payload["gold_set_candidate"])


if __name__ == "__main__":
    unittest.main()
