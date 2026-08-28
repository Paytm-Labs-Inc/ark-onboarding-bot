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


class DegradedFieldTests(unittest.TestCase):
    """The log has to make a degraded answer countable, not just visible."""

    def test_record_carries_stream_provenance(self) -> None:
        record = query_log_module.build_record(
            question="how do I enroll a host?",
            answer="Run ark host enroll",
            citations=[],
            retrieved_sources=["getting-started"],
            top_score=0.61,
            chunk_count=8,
            channel="web",
            stream_mode="pi-stream",
            stream_errors=[],
            degraded="salvaged",
        )
        self.assertEqual(record["stream_mode"], "pi-stream")
        self.assertEqual(record["degraded"], "salvaged")
        self.assertEqual(record["stream_errors"], [])

    def test_record_defaults_when_not_supplied(self) -> None:
        record = query_log_module.build_record(
            question="q",
            answer="a",
            citations=[],
            retrieved_sources=[],
            top_score=None,
            chunk_count=0,
            channel="cli",
        )
        self.assertIsNone(record["stream_mode"])
        self.assertIsNone(record["degraded"])
        self.assertEqual(record["stream_errors"], [])



class ConcurrentAppendTests(unittest.TestCase):
    """The lock's one promise: every line stays valid JSON under concurrent writers.

    A single write() on an O_APPEND handle is atomic on Linux, so a plain
    harness passes with the lock removed (review measured it). Force each
    record through two syscalls with a yield between them, which is the shape
    the lock actually guards against: 122 of 200 lines corrupt without it.
    """

    def test_concurrent_writers_never_interleave_a_line(self) -> None:
        import json, tempfile, threading, time
        from pathlib import Path

        class SplitWritePath(type(Path())):
            def open(self, *args, **kwargs):  # type: ignore[override]
                handle = super().open(*args, **kwargs)
                real_write = handle.write

                def split_write(text):
                    half = len(text) // 2
                    real_write(text[:half])
                    time.sleep(0)  # yield so another writer can interleave
                    return real_write(text[half:])

                handle.write = split_write
                return handle

        with tempfile.TemporaryDirectory() as tmp:
            path = SplitWritePath(tmp) / "q.jsonl"
            with patch.object(query_log_module, "QUERY_LOG_PATH", path):
                big = "x" * 8000
                def worker(i):
                    for j in range(20):
                        query_log_module.append_query_log({"w": i, "j": j, "pad": big})
                threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
                for t in threads: t.start()
                for t in threads: t.join()
                lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 200)
        for line in lines:
            json.loads(line)

    def test_a_held_lock_times_out_as_an_oserror_instead_of_hanging(self) -> None:
        with patch.object(query_log_module, "_WRITE_LOCK_TIMEOUT_SECONDS", 0.05):
            query_log_module._WRITE_LOCK.acquire()
            try:
                with self.assertRaises(OSError):
                    query_log_module.append_query_log({"x": 1})
            finally:
                query_log_module._WRITE_LOCK.release()

if __name__ == "__main__":
    unittest.main()
