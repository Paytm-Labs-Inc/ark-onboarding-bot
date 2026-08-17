"""Tests for the web UI (no live ask() calls)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import feedback as feedback_module
from src.web import app


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.feedback_path = Path(self.temp_dir.name) / "feedback.jsonl"
        self.feedback_patch = patch.object(feedback_module, "FEEDBACK_PATH", self.feedback_path)
        self.feedback_patch.start()

    def tearDown(self) -> None:
        self.feedback_patch.stop()
        self.temp_dir.cleanup()

    def test_index_returns_html(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ark Onboarding Bot", response.text)

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("src.web.retrieve", return_value=[{"source": "x", "text": "y"}])
    @patch("src.web.load_chunks", return_value=[{"source": "x", "text": "y"}])
    def test_ready_returns_ok_when_corpus_loaded(
        self, _mock_chunks: object, _mock_retrieve: object
    ) -> None:
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    @patch("src.web.load_chunks", return_value=[])
    def test_ready_returns_503_when_corpus_empty(self, _mock_chunks: object) -> None:
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["reason"], "no corpus chunks")

    @patch("src.web.ask_in_session")
    def test_api_ask_returns_answer_and_sources(self, mock_ask) -> None:
        mock_ask.return_value = {
            "session_id": "sess-1",
            "answer": "Use ~/.cursor/mcp.json",
            "citations": ["set-up-cursor -- https://example.com/cursor"],
            "retrieved_sources": ["set-up-cursor -- https://example.com/cursor"],
            "sources": [
                {
                    "slug": "set-up-cursor",
                    "url": "https://example.com/cursor",
                    "label": "set-up-cursor -- https://example.com/cursor",
                }
            ],
        }

        response = self.client.post(
            "/api/ask",
            json={"question": "how do I set up Cursor?", "session_id": "sess-1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], "Use ~/.cursor/mcp.json")
        self.assertEqual(len(payload["sources"]), 1)
        mock_ask.assert_called_once_with("sess-1", "how do I set up Cursor?")

    def test_api_ask_rejects_blank_question(self) -> None:
        response = self.client.post("/api/ask", json={"question": "   "})
        self.assertEqual(response.status_code, 400)

    def test_api_feedback_appends_jsonl(self) -> None:
        response = self.client.post(
            "/api/feedback",
            json={
                "question": "how do I set up Cursor?",
                "answer": "Use MCP.",
                "sources": ["set-up-cursor -- https://example.com/cursor"],
                "retrieved_sources": ["set-up-cursor -- https://example.com/cursor"],
                "session_id": "sess-1",
                "rating": "up",
            },
        )
        self.assertEqual(response.status_code, 200)
        lines = self.feedback_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn('"rating": "up"', lines[0])


if __name__ == "__main__":
    unittest.main()
