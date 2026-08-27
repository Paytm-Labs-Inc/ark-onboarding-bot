"""Tests for the web UI (no live ask() calls)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import feedback as feedback_module
from src.web import app, missing_backend_credential


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

    @patch.dict(os.environ, {"PI_API_KEY": "pi-test"})
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
class AskRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        from src import web as web_module
        web_module._ask_hits.clear()
        self.client = TestClient(app)

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": "2"})
    @patch("src.web.ask_in_session", return_value={"answer": "ok", "citations": [], "session_id": "s"})
    def test_third_question_in_a_minute_is_429(self, _ask) -> None:
        for _ in range(2):
            self.assertEqual(self.client.post("/api/ask", json={"question": "q"}).status_code, 200)
        response = self.client.post("/api/ask", json={"question": "q"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("per minute", response.json()["detail"])

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": "0"})
    @patch("src.web.ask_in_session", return_value={"answer": "ok", "citations": [], "session_id": "s"})
    def test_zero_disables_the_limit(self, _ask) -> None:
        for _ in range(5):
            self.assertEqual(self.client.post("/api/ask", json={"question": "q"}).status_code, 200)


class AskRateLimitEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        from src import web as web_module
        web_module._ask_hits.clear()
        self.client = TestClient(app)

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": "1"})
    @patch("src.web.ask_in_session", return_value={"answer": "ok", "citations": [], "session_id": "s"})
    def test_the_window_slides(self, _ask) -> None:
        with patch("src.web._now", side_effect=[0.0, 1.0, 61.5]):
            self.assertEqual(self.client.post("/api/ask", json={"question": "q"}).status_code, 200)
            self.assertEqual(self.client.post("/api/ask", json={"question": "q"}).status_code, 429)
            self.assertEqual(self.client.post("/api/ask", json={"question": "q"}).status_code, 200)

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": "1"})
    def test_the_stream_endpoint_is_limited_too(self) -> None:
        def fake_stream(session_id, question):
            yield {"type": "done", "answer": "ok", "citations": [], "session_id": "s"}
        with patch("src.web.ask_in_session_stream", side_effect=fake_stream):
            self.assertEqual(self.client.post("/api/ask/stream", json={"question": "q"}).status_code, 200)
            self.assertEqual(self.client.post("/api/ask/stream", json={"question": "q"}).status_code, 429)

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": "1"})
    @patch("src.web.ask_in_session", return_value={"answer": "ok", "citations": [], "session_id": "s"})
    def test_distinct_clients_get_distinct_buckets(self, _ask) -> None:
        # Real key derivation: two clients with different peer addresses.
        a = TestClient(app, client=("10.0.0.1", 1234))
        b = TestClient(app, client=("10.0.0.2", 1234))
        self.assertEqual(a.post("/api/ask", json={"question": "q"}).status_code, 200)
        self.assertEqual(b.post("/api/ask", json={"question": "q"}).status_code, 200)
        self.assertEqual(a.post("/api/ask", json={"question": "q"}).status_code, 429)

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": "5"})
    @patch("src.web.ask_in_session", return_value={"answer": "ok", "citations": [], "session_id": "s"})
    def test_a_full_table_sheds_the_least_recent_client(self, _ask) -> None:
        from src import web as web_module
        with patch.object(web_module, "_ASK_MAX_TRACKED_CLIENTS", 2):
            for host in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
                TestClient(app, client=(host, 1)).post("/api/ask", json={"question": "q"})
        self.assertNotIn("10.0.0.1", web_module._ask_hits)
        self.assertIn("10.0.0.3", web_module._ask_hits)

    @patch.dict(os.environ, {"ASK_RATE_LIMIT_PER_MINUTE": ""})
    @patch("src.web.ask_in_session", return_value={"answer": "ok", "citations": [], "session_id": "s"})
    def test_an_empty_limit_variable_does_not_500(self, _ask) -> None:
        self.assertEqual(self.client.post("/api/ask", json={"question": "q"}).status_code, 200)

    def test_proxy_settings(self) -> None:
        from src.web import proxy_settings
        with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": ""}):
            self.assertEqual(proxy_settings(), {"proxy_headers": False})
        with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": "10.42.0.0/16"}):
            self.assertEqual(proxy_settings(), {"proxy_headers": True, "forwarded_allow_ips": "10.42.0.0/16"})
        with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": "*"}):
            with self.assertRaises(SystemExit):
                proxy_settings()

    def test_idle_clients_are_evicted_when_the_table_is_full(self) -> None:
        from src import web as web_module
        with patch.object(web_module, "_ASK_MAX_TRACKED_CLIENTS", 3):
            for i, t in enumerate((0.0, 0.0, 0.0)):
                web_module._ask_hits[f"c{i}"].append(t)
            with web_module._ask_hits_lock:
                web_module._evict_idle_clients(now=100.0)
            self.assertEqual(len(web_module._ask_hits), 0)

class BackendCredentialReadinessTests(unittest.TestCase):
    """A pod with no model credential must not report Ready."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._saved = {k: os.environ.pop(k, None) for k in ("PI_API_KEY", "CURSOR_API_KEY", "ANSWER_BACKEND")}

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_names_the_missing_credential_for_each_backend(self) -> None:
        self.assertIn("PI_API_KEY", missing_backend_credential())
        os.environ["ANSWER_BACKEND"] = "cursor"
        self.assertIn("CURSOR_API_KEY", missing_backend_credential())
        os.environ["CURSOR_API_KEY"] = "crsr_x"
        self.assertIsNone(missing_backend_credential())

    @patch("src.web.check_retrieval_ready", return_value=(True, {"status": "ready", "chunks": 3}))
    def test_ready_is_503_without_the_credential_and_200_with_it(self, _r) -> None:
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertIn("PI_API_KEY", response.json()["detail"])
        os.environ["PI_API_KEY"] = "pi-x"
        self.assertEqual(self.client.get("/ready").status_code, 200)

if __name__ == "__main__":
    unittest.main()
