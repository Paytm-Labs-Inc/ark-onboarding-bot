"""Tests for the feedback reader and the /reviews page."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import feedback as feedback_module
from src.web import app


class ReviewsTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)  # keep auth off for these tests
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "feedback.jsonl"
        self.patch = patch.object(feedback_module, "FEEDBACK_PATH", self.path)
        self.patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp_dir.cleanup()

    def test_read_feedback_newest_first(self) -> None:
        self.path.write_text(
            '{"question":"q1","rating":"up"}\n{"question":"q2","rating":"down"}\n',
            encoding="utf-8",
        )
        records = feedback_module.read_feedback()
        self.assertEqual([r["question"] for r in records], ["q2", "q1"])

    def test_read_feedback_skips_malformed_lines(self) -> None:
        self.path.write_text(
            '{"question":"ok","rating":"up"}\nnot-json\n\n', encoding="utf-8"
        )
        self.assertEqual(len(feedback_module.read_feedback()), 1)

    def test_reviews_page_renders_records(self) -> None:
        self.path.write_text(
            '{"question":"how do I enroll?","answer":"run ark host enroll",'
            '"rating":"up","sources":["getting-started -- https://x"]}\n',
            encoding="utf-8",
        )
        response = self.client.get("/reviews")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Feedback review", response.text)
        self.assertIn("how do I enroll?", response.text)
        self.assertIn("getting-started", response.text)

    def test_reviews_page_empty_state(self) -> None:
        response = self.client.get("/reviews")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No feedback yet", response.text)


if __name__ == "__main__":
    unittest.main()
