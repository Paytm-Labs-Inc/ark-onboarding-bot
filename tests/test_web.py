"""Tests for the web UI (no live ask() calls)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src import feedback as feedback_module
from src import web
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

    @patch.dict(os.environ, {"PI_API_KEY": "pi-test", "ANSWER_BACKEND": "pi"})
    # /ready now also requires a reachable gateway. A unit test has none, and a
    # unit test should never call out, so the check is stubbed here.
    @patch("src.web.unusable_backend_model", return_value=None)
    @patch("src.warmup.retrieve", return_value=[{"source": "x", "text": "y"}])
    @patch("src.warmup.load_chunks", return_value=[{"source": "x", "text": "y"}])
    def test_ready_returns_ok_when_corpus_loaded(
        self, _mock_chunks: object, _mock_retrieve: object, _gateway: object
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
        # "*" is the platform's interim behind a ClusterIP-only Service: accepted,
        # but loudly, so the warning is asserted rather than the refusal.
        import io
        from contextlib import redirect_stderr
        with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": "*"}):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(proxy_settings(), {"proxy_headers": True, "forwarded_allow_ips": "*"})
            self.assertIn("trusts every peer", err.getvalue())

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
        os.environ["ANSWER_BACKEND"] = "groq"
        self.assertIn("not a backend", missing_backend_credential())

    def test_the_gateway_probe_does_not_run_on_the_event_loop(self) -> None:
        # It is a blocking httpx call of up to PI_TIMEOUT_SECONDS. Called inline
        # from the async handler it freezes the only replica's loop whenever the
        # gateway is slow -- the exact condition it exists to detect. It must go
        # through the same thread limiter as the retrieval probe.
        import inspect

        source = inspect.getsource(web.ready)
        self.assertIn("unusable_backend_model", source)
        probe = source[source.index("unusable_backend_model") - 200 :]
        self.assertIn("run_sync", probe)
        self.assertIn("_PROBE_LIMITER", probe)
        self.assertNotIn("unusable = unusable_backend_model()", source)

    # unusable_backend_model is stubbed out: this test is about the credential
    # branch, and the fake key below would otherwise send a real request to the
    # gateway from a unit test. The gateway branch has its own tests.
    @patch("src.web.unusable_backend_model", return_value=None)
    @patch("src.web.check_retrieval_ready", return_value=(True, {"status": "ready", "chunks": 3}))
    def test_ready_is_503_without_the_credential_and_200_with_it(self, _r, _g) -> None:
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertIn("PI_API_KEY", response.json()["reason"])
        os.environ["PI_API_KEY"] = "pi-x"
        self.assertEqual(self.client.get("/ready").status_code, 200)


class SignOutClearsTheStoredChatTests(unittest.TestCase):
    """Signing out must not leave the transcript in the browser.

    The chat is persisted under `ark-onboarding-bot:chat:<base>` -- a key built
    only from the base path, with no user in it -- and /logout is a server-side
    redirect that can only drop the cookie. So signing out and back in restored
    the previous conversation, and on a shared machine showed it to someone who
    was never part of it.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def _template(self, name: str) -> str:
        return (self.ROOT / "src" / "templates" / name).read_text(encoding="utf-8")

    def test_the_login_page_clears_the_stored_chat(self) -> None:
        # The guarantee: every route to an unauthenticated state lands here --
        # the sign-out link, a typed /logout, an expired cookie, the auth
        # redirect -- so the clear belongs on this page, not only on the link.
        login = self._template("login.html")
        self.assertIn("localStorage.removeItem", login)
        self.assertIn("ark-onboarding-bot:chat:", login)

    def test_the_sign_out_link_clears_before_navigating(self) -> None:
        chat = self._template("chat.html")
        self.assertIn('id="sign-out"', chat)
        start = chat.index('getElementById("sign-out")')
        handler = chat[start : start + 240]
        self.assertIn("clearStoredChat()", handler)

    def test_the_storage_key_and_the_clear_agree(self) -> None:
        # The two templates build the key independently; if one drifts the
        # clear silently stops matching what was written.
        chat = self._template("chat.html")
        login = self._template("login.html")
        key = "ark-onboarding-bot:chat:${base}"
        self.assertIn(key, chat, "chat.html no longer builds the expected key")
        self.assertIn(key, login, "login.html clears a different key than chat.html writes")


class ErrorTextAndHeadersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("src.web.ask_in_session", side_effect=RuntimeError("api.inference.paytm.com HTTP 503 for model qwen/qwen3-32b"))
    def test_upstream_error_text_never_reaches_the_user(self, _ask) -> None:
        response = self.client.post("/api/ask", json={"question": "q"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("inference.paytm.com", response.text)
        self.assertNotIn("qwen", response.text)

    @patch("src.web.ask_in_session", side_effect=TimeoutError("read timed out after 20s at api.inference.paytm.com"))
    def test_timeout_text_is_generic(self, _ask) -> None:
        response = self.client.post("/api/ask", json={"question": "q"})
        self.assertEqual(response.status_code, 504)
        self.assertNotIn("inference.paytm.com", response.text)

    def test_security_headers_on_every_response(self) -> None:
        with patch("src.web.ask_in_session", side_effect=RuntimeError("x")):
            responses = [
                self.client.get("/health"),
                self.client.post("/api/ask", json={"question": "q"}),  # 502
                self.client.get("/reviews", follow_redirects=False),  # 303 to login when a token is set, else 200
            ]
        for response in responses:
            headers = response.headers
            self.assertEqual(headers["X-Frame-Options"], "DENY", response.url)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff", response.url)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"], response.url)

    @patch("src.web._log_upstream_failure")
    @patch("src.web.ask_in_session", side_effect=ValueError("PI_API_KEY not set. Set it, or set ANSWER_BACKEND=cursor."))
    def test_config_valueerror_is_logged_and_not_echoed(self, _ask, log) -> None:
        response = self.client.post("/api/ask", json={"question": "q"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("PI_API_KEY", response.text)
        log.assert_called_once()
class ProbesOffTheAnswerPoolTests(unittest.TestCase):
    def test_probes_are_async_so_they_never_wait_on_a_worker_thread(self) -> None:
        import inspect
        from src.web import health, ready
        self.assertTrue(inspect.iscoroutinefunction(health))
        self.assertTrue(inspect.iscoroutinefunction(ready))

    def test_threadpool_size_parses_leniently(self) -> None:
        from src.web import threadpool_size
        with patch.dict(os.environ, {"WEB_THREADPOOL_SIZE": ""}):
            self.assertEqual(threadpool_size(), 64)
        with patch.dict(os.environ, {"WEB_THREADPOOL_SIZE": "128"}):
            self.assertEqual(threadpool_size(), 128)
        with patch.dict(os.environ, {"WEB_THREADPOOL_SIZE": "2"}):
            self.assertEqual(threadpool_size(), 8)  # floor


class ProbeAndSlotWiringTests(unittest.TestCase):
    """The two lines that do the work must fail the suite if removed."""

    def test_ready_runs_its_check_on_the_probe_limiter_not_the_answer_pool(self) -> None:
        from src import web as web_module
        seen = {}

        async def fake_run_sync(fn, *args, limiter=None, **kwargs):
            seen["limiter"] = limiter
            return fn(*args)

        with patch("src.web.anyio.to_thread.run_sync", side_effect=fake_run_sync), \
             patch("src.web.check_retrieval_ready", return_value=(True, {"status": "ready", "chunks": 1})), \
             patch("src.web.unusable_backend_model", return_value=None), \
             patch.dict(os.environ, {"PI_API_KEY": "pi-x", "ANSWER_BACKEND": "pi"}):
            self.assertEqual(TestClient(app).get("/ready").status_code, 200)
        self.assertIs(seen["limiter"], web_module._PROBE_LIMITER)

    @patch("src.web.ask_in_session")
    def test_at_capacity_is_503_with_retry_after(self, mock_ask) -> None:
        from src.answer import PiAtCapacity
        mock_ask.side_effect = PiAtCapacity("busy")
        response = TestClient(app).post("/api/ask", json={"question": "q"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "10")

if __name__ == "__main__":
    unittest.main()


class DesignTokenConsistencyTests(unittest.TestCase):
    """Every screen resolves the same variables, or the page renders unstyled.

    The three screens each carried their own palette before: chat used the Ark
    tokens, login and reviews had hand-picked hexes. They are now injected from
    one file, and this asserts each page actually receives them -- a missing
    marker fails silently, leaving a page whose rules reference variables that
    were never defined.
    """

    def setUp(self) -> None:
        self.client = TestClient(app, follow_redirects=False)

    def test_every_screen_carries_the_shared_tokens(self) -> None:
        for path in ("/", "/login", "/reviews"):
            with self.subTest(path=path):
                body = self.client.get(path).text
                self.assertIn("--ark-violet-500", body, f"{path} lost the token block")
                self.assertIn("--ark-bg", body)
                # The marker must have been substituted, not shipped verbatim.
                self.assertNotIn("<!--TOKENS-->", body, f"{path} shipped the marker")

    def test_ready_fails_when_the_gateway_rejects_the_key(self) -> None:
        """The 2026-09-06 incident: a rotated key the pod never picked up."""
        import src.answer as answer_mod

        answer_mod._gateway_check = (0.0, None)
        os.environ["PI_API_KEY"] = "stale-key"
        with patch("src.answer.httpx.get") as get:
            get.return_value = MagicMock(status_code=401)
            reason = answer_mod.unusable_backend_model()
        self.assertIsNotNone(reason)
        self.assertIn("credential", str(reason))

    def test_ready_fails_when_the_model_is_not_served(self) -> None:
        """The 2026-09-01 incident: qwen/qwen3-32b deregistered under us."""
        import src.answer as answer_mod

        answer_mod._gateway_check = (0.0, None)
        os.environ["PI_API_KEY"] = "k"
        os.environ["PI_MODEL"] = "qwen/qwen3-32b"
        try:
            with patch("src.answer.httpx.get") as get:
                get.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {"data": [{"id": "llama-3.3-70b-versatile"}]},
                )
                reason = answer_mod.unusable_backend_model()
        finally:
            os.environ.pop("PI_MODEL", None)
        self.assertIsNotNone(reason)
        self.assertIn("not served", str(reason))

    def test_ready_survives_a_transient_gateway_failure(self) -> None:
        """A blip must NOT take the single replica out of service.

        Failing readiness on a timeout or a 5xx would turn a slow gateway into
        an ingress 503 with no friendly message, for a condition the
        per-request retry already handles. Only auth and a missing model are
        certainly fatal and certainly persistent.
        """
        import src.answer as answer_mod

        os.environ["PI_API_KEY"] = "k"
        for failure in (TimeoutError("read timeout"), None):
            with self.subTest(failure=failure):
                answer_mod._gateway_check = (0.0, None)
                with patch("src.answer.httpx.get") as get:
                    if failure is None:
                        get.return_value = MagicMock(status_code=503)
                    else:
                        get.side_effect = failure
                    self.assertIsNone(answer_mod.unusable_backend_model())

    def test_the_gateway_check_is_cached(self) -> None:
        """kubelet polls /ready every few seconds; an uncached check is traffic."""
        import src.answer as answer_mod

        answer_mod._gateway_check = (0.0, None)
        os.environ["PI_API_KEY"] = "k"
        with patch("src.answer.httpx.get") as get:
            get.return_value = MagicMock(status_code=200, json=lambda: {"data": []})
            for _ in range(5):
                answer_mod.unusable_backend_model(now=1000.0)
        self.assertEqual(get.call_count, 1, "the probe must not call out every time")

    def test_new_chat_cannot_be_undone_by_an_in_flight_answer(self) -> None:
        """Regression: an abandoned ask used to rewrite the cleared store.

        New chat runs clearStoredChat and nulls sessionId, but an ask already in
        flight still resolved and called persistCompletedTurn, so a refresh
        restored the conversation the user had just cleared. The fix is a
        generation counter: the ask captures it at submit and discards its own
        result if New chat has bumped it. Asserted on the served page because
        this behaviour lives in the template, and there is no JS test harness.
        """
        body = self.client.get("/").text
        self.assertIn("let chatGeneration = 0;", body)
        self.assertIn("const generation = chatGeneration;", body)
        self.assertIn("chatGeneration += 1;", body, "New chat must bump the generation")
        # The guard has to sit BEFORE the session id is adopted and the turn is
        # persisted, or it does not prevent either.
        guard = body.index("if (generation !== chatGeneration)")
        self.assertLess(guard, body.index("sessionId = payload.session_id;"))
        # the CALL site, not the function definition, which sits far earlier
        self.assertLess(guard, body.index("persistCompletedTurn(question, payload.answer"))

        # The guard must ABORT, not merely exist. Presence-and-order assertions
        # survive neutralising the body -- ArkBot demonstrated exactly that
        # mutation staying green on the first version of this test. Read the
        # block between the guard's braces and require a bare `return`.
        open_brace = body.index("{", guard)
        depth, i = 0, open_brace
        while i < len(body):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = body[open_brace + 1 : i]
        self.assertRegex(
            block, r"(?m)^\s*return\s*;",
            "the generation guard must return; a guard that falls through does nothing",
        )

    def test_no_screen_hardcodes_the_old_palette(self) -> None:
        """The hexes the login and reviews pages used before they shared tokens."""
        for path in ("/", "/login", "/reviews"):
            with self.subTest(path=path):
                body = self.client.get(path).text
                for stale in ("#f6f7f9", "#2563eb", "#1f2937"):
                    self.assertNotIn(stale, body, f"{path} still hardcodes {stale}")
