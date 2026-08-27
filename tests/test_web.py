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
        self.warm_patch = patch("src.web.warm_services")
        self.warm_patch.start()
        self.client = TestClient(app)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.feedback_path = Path(self.temp_dir.name) / "feedback.jsonl"
        self.feedback_patch = patch.object(feedback_module, "FEEDBACK_PATH", self.feedback_path)
        self.feedback_patch.start()

    def tearDown(self) -> None:
        self.feedback_patch.stop()
        self.warm_patch.stop()
        self.temp_dir.cleanup()

    def test_index_returns_html(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ark Onboarding Bot", response.text)

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("src.warmup.retrieve", return_value=[{"source": "x", "text": "y"}])
    @patch("src.warmup.load_chunks", return_value=[{"source": "x", "text": "y"}])
    def test_ready_returns_ok_when_corpus_loaded(
        self, _mock_chunks: object, _mock_retrieve: object
    ) -> None:
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    @patch("src.warmup.load_chunks", return_value=[])
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

    @patch("src.web.ask_in_session_stream")
    def test_api_ask_stream_emits_sse_events(self, mock_stream) -> None:
        def fake_stream(_session_id, _question):
            yield {"type": "delta", "text": "Run "}
            yield {
                "type": "done",
                "session_id": "sess-2",
                "answer": "Run ark host enroll.",
                "citations": ["getting-started -- https://x"],
                "retrieved_sources": ["getting-started -- https://x"],
                "sources": [
                    {
                        "slug": "getting-started",
                        "url": "https://x",
                        "label": "getting-started -- https://x",
                    }
                ],
            }

        mock_stream.side_effect = fake_stream

        with self.client.stream(
            "POST",
            "/api/ask/stream",
            json={"question": "how do I enroll a host?", "session_id": "sess-2"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers.get("content-type", ""))
            body = "".join(response.iter_text())

        self.assertIn('"type": "delta"', body)
        self.assertIn('"type": "done"', body)
        self.assertIn("Run ark host enroll.", body)
        mock_stream.assert_called_once_with("sess-2", "how do I enroll a host?")

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



class FeedbackWriteFailureTests(unittest.TestCase):
    @patch("src.web.append_feedback", side_effect=OSError("disk full"))
    def test_feedback_reports_503_not_500(self, _a) -> None:
        response = TestClient(app).post(
            "/api/feedback",
            json={"question": "q", "answer": "a", "rating": "up"},
        )
        self.assertEqual(response.status_code, 503)

if __name__ == "__main__":
    unittest.main()
