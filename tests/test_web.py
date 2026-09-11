"""Tests for the web UI (no live ask() calls)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src import feedback as feedback_module
from src import auth, web
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
        mock_ask.assert_called_once()
        self.assertEqual(mock_ask.call_args.args[0], "sess-1")
        self.assertEqual(mock_ask.call_args.args[1], "how do I set up Cursor?")
        self.assertEqual(
            mock_ask.call_args.kwargs["user_id"],
            self.client.cookies[auth.BROWSER_ID_COOKIE],
        )

    @patch("src.web.ask_in_session_stream")
    def test_api_ask_stream_emits_sse_events(self, mock_stream) -> None:
        def fake_stream(_session_id, _question, user_id=None):
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
        mock_stream.assert_called_once()
        self.assertEqual(mock_stream.call_args.args[0], "sess-2")
        self.assertEqual(mock_stream.call_args.args[1], "how do I enroll a host?")
        self.assertEqual(
            mock_stream.call_args.kwargs["user_id"],
            self.client.cookies[auth.BROWSER_ID_COOKIE],
        )

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


class SessionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)
        # These tests run with SSO configured so they exercise the identity
        # current_user_id() prefers. The cookie fallback -- what production uses
        # until the ingress gate is flipped -- is covered by
        # BrowserScopedSessionTests below.
        os.environ["SSO_IDENTITY_HEADER"] = "X-SSO-User"
        from src.session_store import MemorySessionStore, reset_session_store

        reset_session_store(MemorySessionStore())
        self.warm_patch = patch("src.web.warm_services")
        self.warm_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from src.session_store import reset_session_store

        self.warm_patch.stop()
        reset_session_store(None)
        os.environ.pop("SSO_IDENTITY_HEADER", None)

    @patch("src.web.ask_in_session")
    def test_get_session_returns_saved_turns(self, mock_ask) -> None:
        from src.session_store import (
            MemorySessionStore,
            StoredSession,
            StoredTurn,
            reset_session_store,
            title_from_question,
        )

        mock_ask.return_value = {
            "session_id": "sess-x",
            "answer": "hello",
            "citations": [],
            "retrieved_sources": [],
            "sources": [],
            "handoff": False,
        }
        self.client.post("/api/ask", json={"question": "hi"})
        sid = mock_ask.return_value["session_id"]
        browser_id = self.client.cookies[auth.BROWSER_ID_COOKIE]
        store = MemorySessionStore()
        store.save(
            StoredSession(
                session_id=sid,
                user_id=browser_id,
                title=title_from_question("hi"),
                turns=[StoredTurn("hi", "hello", [], [])],
            )
        )
        reset_session_store(store)

        response = self.client.get(f"/api/session/{sid}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session_id"], sid)
        self.assertEqual(len(body["turns"]), 1)

    def test_get_missing_session_404(self) -> None:
        response = self.client.get("/api/session/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_list_sessions_empty(self) -> None:
        response = self.client.get("/api/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sessions"], [])

    def test_list_archived_sessions_empty(self) -> None:
        response = self.client.get("/api/sessions?archived=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sessions"], [])

    @patch("src.web.ask_in_session")
    def test_reset_missing_session_404(self, mock_ask) -> None:
        response = self.client.post("/api/reset", json={"session_id": "missing"})
        self.assertEqual(response.status_code, 404)

    def test_archive_moves_session_to_archived_list(self) -> None:
        from src.session_store import (
            MemorySessionStore,
            StoredSession,
            StoredTurn,
            reset_session_store,
            title_from_question,
        )

        browser_id = self.client.cookies.get(auth.BROWSER_ID_COOKIE)
        if not browser_id:
            self.client.get("/")
            browser_id = self.client.cookies[auth.BROWSER_ID_COOKIE]
        store = MemorySessionStore()
        store.save(
            StoredSession(
                session_id="sess-archive",
                user_id=browser_id,
                title=title_from_question("what is ark?"),
                turns=[StoredTurn("what is ark?", "Ark is...", [], [])],
            )
        )
        reset_session_store(store)

        response = self.client.post("/api/session/sess-archive/archive")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        active = self.client.get("/api/sessions?archived=false").json()["sessions"]
        archived = self.client.get("/api/sessions?archived=true").json()["sessions"]
        self.assertEqual([item["session_id"] for item in active], [])
        self.assertEqual([item["session_id"] for item in archived], ["sess-archive"])

    def test_unarchive_moves_session_back_to_recent(self) -> None:
        from src.session_store import (
            MemorySessionStore,
            StoredSession,
            StoredTurn,
            reset_session_store,
            title_from_question,
        )

        browser_id = self.client.cookies.get(auth.BROWSER_ID_COOKIE)
        if not browser_id:
            self.client.get("/")
            browser_id = self.client.cookies[auth.BROWSER_ID_COOKIE]
        store = MemorySessionStore()
        store.save(
            StoredSession(
                session_id="sess-unarchive",
                user_id=browser_id,
                title=title_from_question("how to enroll?"),
                turns=[StoredTurn("how to enroll?", "Run ark host enroll.", [], [])],
                archived=True,
                archived_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 86400)),
            )
        )
        reset_session_store(store)

        response = self.client.post("/api/session/sess-unarchive/unarchive")
        self.assertEqual(response.status_code, 200)

        active = self.client.get("/api/sessions?archived=false").json()["sessions"]
        archived = self.client.get("/api/sessions?archived=true").json()["sessions"]
        self.assertEqual([item["session_id"] for item in active], ["sess-unarchive"])
        self.assertEqual(archived, [])

    def test_archive_missing_session_404(self) -> None:
        response = self.client.post("/api/session/missing/archive")
        self.assertEqual(response.status_code, 404)

    def test_expired_archived_session_get_404s(self) -> None:
        from src.session_store import (
            MemorySessionStore,
            StoredSession,
            StoredTurn,
            reset_session_store,
            title_from_question,
        )

        browser_id = self.client.cookies.get(auth.BROWSER_ID_COOKIE)
        if not browser_id:
            self.client.get("/")
            browser_id = self.client.cookies[auth.BROWSER_ID_COOKIE]
        old_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
        store = MemorySessionStore()
        store.save(
            StoredSession(
                session_id="sess-expired",
                user_id=browser_id,
                title=title_from_question("old thread"),
                turns=[StoredTurn("old thread", "gone", [], [])],
                archived=True,
                archived_at=old_time,
                created_at=old_time,
                updated_at=old_time,
            ),
            touch_activity=False,
        )
        reset_session_store(store)

        response = self.client.get("/api/session/sess-expired")
        self.assertEqual(response.status_code, 404)


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
        def fake_stream(session_id, question, user_id=None):
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

    def test_logout_signals_the_sign_out_to_the_login_page(self) -> None:
        client = TestClient(app)
        response = client.get("/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("signed_out=1", response.headers["location"])

    def test_the_login_page_clears_only_on_a_real_sign_out(self) -> None:
        # /login is public and reachable with a valid cookie -- Back after
        # signing in, a bookmark, a second tab. Clearing on every load would
        # wipe a conversation the user never left.
        login = self._template("login.html")
        self.assertIn("localStorage.removeItem", login)
        clear_at = login.index("localStorage.removeItem")
        guard = login[max(0, clear_at - 400) : clear_at]
        self.assertIn("signed_out", guard, "the clear is not gated on the sign-out signal")

    def test_the_sign_out_link_clears_before_navigating(self) -> None:
        chat = self._template("chat.html")
        self.assertIn('id="sign-out"', chat)
        start = chat.index('getElementById("sign-out")')
        handler = chat[start : start + 240]
        self.assertIn("clearStoredChat()", handler)

    def test_the_sign_out_flag_does_not_survive_the_page(self) -> None:
        # Left in the URL the flag outlives the sign-out: a refresh, a Back, or a
        # bookmark would re-run the clear and wipe a chat started since.
        login = self._template("login.html")
        self.assertIn("searchParams.delete", login)
        self.assertIn("replaceState", login)

    def test_the_storage_key_and_the_clear_agree(self) -> None:
        # The two templates build the key independently; if one drifts the
        # clear silently stops matching what was written.
        chat = self._template("chat.html")
        login = self._template("login.html")
        key = "ark-onboarding-bot:chat:${base}"
        self.assertIn(key, chat, "chat.html no longer builds the expected key")
        self.assertIn(key, login, "login.html clears a different key than chat.html writes")


class ArchiveHeaderShowsLoadFailureTests(unittest.TestCase):
    """A failed archive fetch must not read as an empty archive.

    The count/hint live on the always-visible <summary>. Feeding it 0 on
    HTTP/network failure wrote "No archived chats" on the header while
    "Could not load archive" sat inside the collapsed <details>.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_failed_archive_fetch_updates_the_visible_hint(self) -> None:
        chat = (self.ROOT / "src" / "templates" / "chat.html").read_text(encoding="utf-8")
        fn_start = chat.index("function updateArchiveCount")
        fn = chat[fn_start : fn_start + 700]
        self.assertIn("loadFailed", fn)
        self.assertIn("Could not load archive", fn)
        self.assertIn("updateArchiveCount(0, { loadFailed: true })", chat)
        self.assertNotIn(
            "updateArchiveCount(0);",
            chat,
            "a bare count of 0 on failure still paints the header as empty",
        )


class SidebarTitleOverflowTests(unittest.TestCase):
    """A long unbroken title must shrink inside the rail, not scroll it.

    Flex items default to min-width: auto, so the row cannot shrink below its
    text and the title ellipsis never engages. Production showed 283px of row
    in a 255px sidebar.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_session_row_can_shrink_below_its_text(self) -> None:
        chat = (self.ROOT / "src" / "templates" / "chat.html").read_text(encoding="utf-8")
        list_css = chat[chat.index(".session-list {") : chat.index(".session-item {")]
        self.assertIn("overflow-x: hidden", list_css)
        item_css = chat[chat.index(".session-item {") : chat.index(".session-item:hover")]
        self.assertIn("min-width: 0", item_css)
        li_css = chat[chat.index(".session-list li {") : chat.index(".session-archive,")]
        self.assertIn("min-width: 0", li_css)
        title_css = chat[chat.index(".session-title {") : chat.index(".session-meta {")]
        self.assertIn("width: 100%", title_css)
        self.assertIn("text-overflow: ellipsis", title_css)
        meta_css = chat[chat.index(".session-meta {") : chat.index(".session-empty {")]
        self.assertIn("width: 100%", meta_css)
        self.assertIn("text-overflow: ellipsis", meta_css)
        archive_css = chat[chat.index(".session-archive,") : chat.index(".session-archive svg,")]
        self.assertIn("flex: none", archive_css)
        self.assertIn("min-width: 24px", archive_css)


class ArchiveUnarchiveControlTests(unittest.TestCase):
    """Archived rows need a way back to Recent, not only the 5-second toast.

    Opening an archived chat does not unarchive it. Without a control on the
    row, Undo was the only restore after the toast vanished.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_archived_list_wires_an_unarchive_control(self) -> None:
        chat = (self.ROOT / "src" / "templates" / "chat.html").read_text(encoding="utf-8")
        self.assertIn("showUnarchiveButton: true", chat)
        self.assertIn("Unarchive chat:", chat)
        self.assertIn("session-unarchive", chat)

    def test_undo_toast_sits_at_the_bottom(self) -> None:
        chat = (self.ROOT / "src" / "templates" / "chat.html").read_text(encoding="utf-8")
        toast = chat[chat.index(".archive-toast {") : chat.index(".archive-toast.hidden")]
        self.assertIn("bottom:", toast)
        self.assertIn("left: 50%", toast)
        self.assertNotIn("top: 50%", toast)


class BootstrapClearsPurgedSessionTests(unittest.TestCase):
    """A 404 on the stored session id must not keep that id for the next ask.

    Retention deletes an archived thread inside load(), so GET /api/session
    404s. bootstrapChat used to leave sessionId and the painted transcript;
    the next ask sent the dead id and get_session minted a new UUID.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_bootstrap_clears_on_session_404(self) -> None:
        chat = (self.ROOT / "src" / "templates" / "chat.html").read_text(encoding="utf-8")
        start = chat.index("async function bootstrapChat()")
        bootstrap = chat[start : chat.index("async function archiveSession(")]
        not_ok = bootstrap[bootstrap.index("response.status === 404") :]
        self.assertIn("clearStoredChat()", not_ok)
        self.assertIn("showEmptyState()", not_ok)
        self.assertIn("sessionId = null", not_ok)
        # An in-flight ask captured the old generation. Wiping without keeping
        # the live turn dropped the streaming answer. If the ask already
        # adopted a new id, this 404 must not undo that. Do not bump
        # chatGeneration here: that discarded an in-flight openSession.
        self.assertIn("sessionId !== bootstrappedId", not_ok)
        self.assertIn("submitBtn.disabled", not_ok)
        self.assertIn("chat.appendChild(live)", not_ok)
        self.assertNotIn("chatGeneration += 1", not_ok)


class ReadyReportsWhatItVerifiedTests(unittest.TestCase):
    """A green probe must say what it checked.

    Before this, the success body was identical whether the gateway had been
    verified or the check did not exist -- the same ambiguity that let two
    outages sit behind a green /ready: "the key is set" read the same as "the
    model answers".
    """

    @patch("src.web.unusable_backend_model", return_value=None)
    @patch("src.web.check_retrieval_ready", return_value=(True, {"status": "ready", "chunks": 393}))
    def test_a_ready_probe_names_the_model_and_the_gateway(self, _r, _g) -> None:
        os.environ["PI_API_KEY"] = "pi-x"
        os.environ["PI_MODEL"] = "llama-3.3-70b-versatile"
        try:
            payload = TestClient(app).get("/ready").json()
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["chunks"], 393)
            self.assertEqual(payload["model"], "llama-3.3-70b-versatile")
            self.assertEqual(payload["gateway"], "ok")
        finally:
            os.environ.pop("PI_MODEL", None)

    @patch("src.web.unusable_backend_model", return_value="model 'x' is not served by the gateway")
    @patch("src.web.check_retrieval_ready", return_value=(True, {"status": "ready", "chunks": 393}))
    def test_a_failing_gateway_still_reports_the_reason_and_no_ok(self, _r, _g) -> None:
        # The failure path must not claim gateway ok -- that would be worse than
        # saying nothing.
        os.environ["PI_API_KEY"] = "pi-x"
        response = TestClient(app).get("/ready")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "not_ready")
        self.assertIn("not served", payload["reason"])
        self.assertNotIn("gateway", payload)


class BrowserIdentityTests(unittest.TestCase):
    """A per-browser id so chats have an owner before SSO exists.

    Identity, never authorisation. The distinction is the whole test class:
    anyone can send a cookie, so if this ever gated access, clearing cookies
    would become a way in.
    """

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._token = os.environ.pop("ARK_ACCESS_TOKEN", None)

    def tearDown(self) -> None:
        if self._token is not None:
            os.environ["ARK_ACCESS_TOKEN"] = self._token
        else:
            os.environ.pop("ARK_ACCESS_TOKEN", None)

    def test_a_browser_without_an_id_is_given_one(self) -> None:
        response = self.client.get("/health")
        self.assertIn(auth.BROWSER_ID_COOKIE, response.cookies)
        self.assertRegex(response.cookies[auth.BROWSER_ID_COOKIE], r"^[A-Za-z0-9_-]{16,64}$")

    def test_the_id_is_not_readable_by_the_page(self) -> None:
        # Nothing in the page needs it, and a value scripts cannot read is one
        # an injected script cannot use to identify a user.
        header = self.client.get("/health").headers.get("set-cookie", "")
        self.assertIn("httponly", header.lower())

    def test_a_malformed_id_is_rejected_not_used(self) -> None:
        # It is attacker-controlled and will build storage keys; a path
        # separator or wildcard must never reach the store.
        for bad in ("../../etc", "a" * 200, "has space", "*", ""):
            request = SimpleNamespace(cookies={auth.BROWSER_ID_COOKIE: bad}, headers={})
            self.assertIsNone(auth.browser_identity(request), bad)

    def test_sso_identity_wins_over_the_browser_id(self) -> None:
        # Once the gate is on, the person must beat the machine they sat at.
        os.environ["SSO_IDENTITY_HEADER"] = "X-Auth-Request-Email"
        try:
            request = SimpleNamespace(
                cookies={auth.BROWSER_ID_COOKIE: "browser0000000000"},
                headers={"X-Auth-Request-Email": "someone@paytm.com"},
            )
            self.assertEqual(auth.current_identity(request), "someone@paytm.com")
        finally:
            os.environ.pop("SSO_IDENTITY_HEADER", None)

    def test_a_browser_id_alone_is_not_authorisation(self) -> None:
        # The one that matters. With a token configured, a request carrying only
        # a browser id must still be refused -- otherwise "clear your cookies"
        # becomes a way past the gate.
        os.environ["ARK_ACCESS_TOKEN"] = "secret-token"
        try:
            client = TestClient(app)
            # /reviews, not /api/sessions: that route does not exist on main yet,
            # so ANY request to it 404s and this test would pass whether or not
            # the gate held -- proving nothing while reading as though it did
            # (Bugbot). Assert against a route that exists and is gated, so the
            # only way to reach 200 is to have been let through.
            with_id = client.get(
                "/reviews",
                cookies={auth.BROWSER_ID_COOKIE: "browser0000000000"},
                follow_redirects=False,
            )
            self.assertEqual(with_id.status_code, 303, "a browser id must not be a way in")
            self.assertIn("/login", with_id.headers.get("location", ""))

            # And the control: the same route DOES serve when the real token is
            # presented, so the assertion above is about the browser id and not
            # about the route being broken for everyone.
            with_token = client.get(
                "/reviews",
                cookies={auth.COOKIE_NAME: "secret-token"},
                follow_redirects=False,
            )
            self.assertEqual(with_token.status_code, 200)
        finally:
            os.environ.pop("ARK_ACCESS_TOKEN", None)


class BrowserScopedSessionTests(unittest.TestCase):
    """Isolation must hold on the cookie alone, before SSO is flipped.

    Shipping the sidebar on a per-browser id is only defensible if one browser
    genuinely cannot read another's threads. These seed the store directly
    rather than driving /api/ask: mocking the ask path writes no session, which
    would leave the list assertions passing against an empty store and proving
    nothing.
    """

    MINE = "browseraaaaaaaaaa"
    THEIRS = "browserbbbbbbbbbb"

    def setUp(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)
        os.environ.pop("SSO_IDENTITY_HEADER", None)
        from src.session_store import MemorySessionStore, reset_session_store

        self.store = MemorySessionStore()
        reset_session_store(self.store)
        self.warm_patch = patch("src.web.warm_services")
        self.warm_patch.start()

    def tearDown(self) -> None:
        from src.session_store import reset_session_store

        self.warm_patch.stop()
        reset_session_store(None)

    def _seed(self, user_id: str, session_id: str) -> str:
        from src.session_store import StoredSession, StoredTurn

        self.store.save(
            StoredSession(
                session_id=session_id,
                user_id=user_id,
                title="how do I set up Cursor?",
                turns=[
                    StoredTurn(
                        question="how do I set up Cursor?",
                        answer="Run ark init.",
                        citations=[],
                        retrieved_sources=[],
                    )
                ],
            )
        )
        return session_id

    def _client_for(self, browser_id: str) -> TestClient:
        client = TestClient(app)
        client.cookies.set(auth.BROWSER_ID_COOKIE, browser_id)
        return client

    def test_the_list_is_served_without_sso(self) -> None:
        # The removed gate returned 503 here, which hid the sidebar entirely.
        self._seed(self.MINE, "s-mine-1")
        response = self._client_for(self.MINE).get("/api/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [s["session_id"] for s in response.json()["sessions"]], ["s-mine-1"]
        )

    def test_one_browser_cannot_list_anothers_threads(self) -> None:
        self._seed(self.MINE, "s-mine-1")
        self._seed(self.THEIRS, "s-theirs-1")

        # Asserted against a store that demonstrably holds both, so an empty
        # result here is scoping and not an empty store.
        listed = self._client_for(self.THEIRS).get("/api/sessions").json()["sessions"]
        self.assertEqual([s["session_id"] for s in listed], ["s-theirs-1"])

    def test_a_thread_from_another_browser_is_404_not_403(self) -> None:
        # 404 rather than 403: the id is the capability, and distinguishing
        # "not yours" from "does not exist" would confirm the thread exists.
        self._seed(self.MINE, "s-mine-1")

        theirs = self._client_for(self.THEIRS)
        self.assertEqual(theirs.get("/api/session/s-mine-1").status_code, 404)
        # Same status as a thread that was never created at all.
        self.assertEqual(theirs.get("/api/session/s-nope").status_code, 404)

    def test_the_sso_cutover_does_not_re_key_existing_threads(self) -> None:
        """Documents the cutover cost, because the code comment once denied it.

        Threads carry the browser id in stored user_id. Flipping the ingress
        gate changes which identity current_user_id() PREFERS, not what is
        already on disk -- so on that day every existing sidebar empties and
        every existing thread 404s. New threads key to the person correctly.

        This is a real product decision (backfill, or tell people history
        restarts) and it belongs to the SSO rollout. The test exists so the
        decision is made rather than discovered in production.
        """
        self._seed(self.MINE, "s-mine-1")
        client = self._client_for(self.MINE)
        self.assertEqual(
            [s["session_id"] for s in client.get("/api/sessions").json()["sessions"]],
            ["s-mine-1"],
        )

        # The gate goes on; same browser, same cookie, now with an identity.
        os.environ["SSO_IDENTITY_HEADER"] = "X-SSO-User"
        try:
            headers = {"X-SSO-User": "someone@paytm.com"}
            self.assertEqual(
                client.get("/api/sessions", headers=headers).json()["sessions"], []
            )
            self.assertEqual(
                client.get("/api/session/s-mine-1", headers=headers).status_code, 404
            )
        finally:
            os.environ.pop("SSO_IDENTITY_HEADER", None)

        # The thread is not lost, only unreachable under the new identity.
        self.assertIsNotNone(self.store.load("s-mine-1"))

    def test_another_browser_cannot_delete_your_thread(self) -> None:
        """The delete path deserves the same proof as the read paths.

        /api/reset is the only endpoint that destroys data, and its owner check
        was the one nothing pinned: removing `or stored.user_id != user_id`
        from reset_session left the whole suite green, while the same deletion
        in load_session_payload went red immediately.

        The status code is the lesser assertion here. What matters is that the
        thread is still in the store afterwards.
        """
        self._seed(self.MINE, "s-mine-1")

        theirs = self._client_for(self.THEIRS)
        response = theirs.post("/api/reset", json={"session_id": "s-mine-1"})
        self.assertEqual(response.status_code, 404)

        # The point of the test: the refusal actually protected the data.
        self.assertIsNotNone(self.store.load("s-mine-1"))

    def test_the_owner_can_delete_their_own_thread(self) -> None:
        # Without this, the refusal above would pass just as well if reset
        # were broken for everyone.
        self._seed(self.MINE, "s-mine-1")
        response = self._client_for(self.MINE).post(
            "/api/reset", json={"session_id": "s-mine-1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.load("s-mine-1"))

    def test_the_owner_can_read_their_own_thread(self) -> None:
        # Without this the 404s above would also pass if reads were simply broken.
        self._seed(self.MINE, "s-mine-1")
        response = self._client_for(self.MINE).get("/api/session/s-mine-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session_id"], "s-mine-1")


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
        with patch.dict(os.environ, {"PI_API_KEY": "stale-key", "ANSWER_BACKEND": "pi"}):
            with patch("src.answer.httpx.get") as get:
                get.return_value = MagicMock(status_code=401)
                reason = answer_mod.unusable_backend_model()
        self.assertIsNotNone(reason)
        self.assertIn("credential", str(reason))

    def test_ready_fails_when_the_model_is_not_served(self) -> None:
        """The 2026-09-01 incident: qwen/qwen3-32b deregistered under us."""
        import src.answer as answer_mod

        answer_mod._gateway_check = (0.0, None)
        with patch.dict(os.environ, {"PI_API_KEY": "k", "PI_MODEL": "qwen/qwen3-32b", "ANSWER_BACKEND": "pi"}):
            with patch("src.answer.httpx.get") as get:
                get.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {"data": [{"id": "llama-3.3-70b-versatile"}]},
                )
                reason = answer_mod.unusable_backend_model()
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

        with patch.dict(os.environ, {"PI_API_KEY": "k", "ANSWER_BACKEND": "pi"}):
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
        with patch.dict(os.environ, {"PI_API_KEY": "k", "ANSWER_BACKEND": "pi"}):
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
        # Scope to the ask submit handler: bootstrap/openSession also adopt
        # session ids, and the guard must sit before those in the in-flight path.
        submit = body[body.index('form.addEventListener("submit"'):]
        guard = submit.index("if (generation !== chatGeneration)")
        self.assertLess(guard, submit.index("sessionId = payload.session_id;"))
        self.assertLess(guard, submit.index("persistCompletedTurn(question, payload.answer"))
        guard += body.index('form.addEventListener("submit"')

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
