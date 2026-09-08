"""Tests for session persistence (memory and Redis-shaped stores)."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from src.chat import ask_in_session, list_user_sessions, load_session_payload, reset_session
from src.session_store import (
    MemorySessionStore,
    StoredSession,
    StoredTurn,
    extract_ark_session_id,
    reset_session_store,
)


class ExtractArkSessionIdTests(unittest.TestCase):
    def test_finds_session_id_in_question(self) -> None:
        self.assertEqual(
            extract_ark_session_id("My flow failed on s-1j4dq5biie — what happened?"),
            "s-1j4dq5biie",
        )

    def test_normalizes_case(self) -> None:
        self.assertEqual(extract_ark_session_id("see S-ABC12345"), "s-abc12345")

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(extract_ark_session_id("how do I enroll a host?"))


class MemorySessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemorySessionStore()
        reset_session_store(self.store)

    def tearDown(self) -> None:
        reset_session_store(None)

    def test_save_and_load_round_trip(self) -> None:
        stored = StoredSession(
            session_id="sess-1",
            user_id="user-a",
            title="Hello",
            linked_ark_session_id="s-1j4dq5biie",
            turns=[StoredTurn("q1", "a1", ["c1"], ["r1"])],
        )
        self.store.save(stored)
        loaded = self.store.load("sess-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.turns[0].question, "q1")
        self.assertEqual(loaded.user_id, "user-a")
        self.assertEqual(loaded.linked_ark_session_id, "s-1j4dq5biie")

    def test_delete_removes_session(self) -> None:
        self.store.save(StoredSession(session_id="sess-2", user_id="user-a", turns=[]))
        self.store.delete("sess-2")
        self.assertIsNone(self.store.load("sess-2"))

    def test_list_for_user_returns_sessions_newest_first(self) -> None:
        first = StoredSession(
            session_id="sess-a",
            user_id="user-a",
            title="First",
            created_at="2026-09-01T10:00:00Z",
            updated_at="2026-09-01T10:00:00Z",
            turns=[StoredTurn("q", "a", [], [])],
        )
        second = StoredSession(
            session_id="sess-b",
            user_id="user-a",
            title="Second",
            created_at="2026-09-02T10:00:00Z",
            updated_at="2026-09-02T10:00:00Z",
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(first)
        self.store.save(second)
        listed = self.store.list_for_user("user-a", archived=False)
        self.assertEqual(len(listed), 2)
        self.assertEqual(
            {item.session_id for item in listed},
            {"sess-a", "sess-b"},
        )

    def test_list_for_user_active_and_archived(self) -> None:
        active = StoredSession(
            session_id="active-1",
            user_id="user-a",
            title="Active chat",
            turns=[StoredTurn("q", "a", [], [])],
        )
        archived = StoredSession(
            session_id="arch-1",
            user_id="user-a",
            title="Old chat",
            archived=True,
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(active)
        self.store.save(archived)
        active_list = self.store.list_for_user("user-a", archived=False)
        archived_list = self.store.list_for_user("user-a", archived=True)
        self.assertEqual([item.session_id for item in active_list], ["active-1"])
        self.assertEqual([item.session_id for item in archived_list], ["arch-1"])

    def test_archive_idle_sessions(self) -> None:
        os.environ["SESSION_ARCHIVE_AFTER_DAYS"] = "7"
        old_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
        session = StoredSession(
            session_id="idle-1",
            user_id="user-a",
            title="Idle",
            created_at=old_time,
            updated_at=old_time,
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store._sessions[session.session_id] = session
        self.store._user_active.setdefault("user-a", {})[session.session_id] = (
            time.time() - 8 * 86400
        )
        active_list = self.store.list_for_user("user-a", archived=False)
        archived_list = self.store.list_for_user("user-a", archived=True)
        self.assertEqual(active_list, [])
        self.assertEqual([item.session_id for item in archived_list], ["idle-1"])
        os.environ.pop("SESSION_ARCHIVE_AFTER_DAYS", None)


class ChatPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_store(MemorySessionStore())

    def tearDown(self) -> None:
        reset_session_store(None)

    @patch("src.chat.ask")
    def test_session_survives_store_reload(self, mock_ask) -> None:
        mock_ask.return_value = {
            "answer": "Run ark host enroll.",
            "citations": [],
            "retrieved_sources": [],
        }
        first = ask_in_session(None, "how do I enroll?", user_id="user-a")
        sid = first["session_id"]

        from src.session_store import get_session_store

        reloaded = get_session_store().load(sid)
        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        self.assertEqual(reloaded.user_id, "user-a")
        self.assertEqual(len(reloaded.turns), 1)

    @patch("src.chat.ask", return_value={"answer": "ok", "citations": [], "retrieved_sources": []})
    def test_reset_deletes_persisted_session(self, _ask) -> None:
        first = ask_in_session(None, "q?", user_id="user-a")
        self.assertTrue(reset_session(first["session_id"], user_id="user-a"))
        from src.session_store import get_session_store

        self.assertIsNone(get_session_store().load(first["session_id"]))

    @patch("src.chat.ask", return_value={"answer": "ok", "citations": [], "retrieved_sources": []})
    def test_user_cannot_load_other_users_session(self, _ask) -> None:
        first = ask_in_session(None, "q?", user_id="user-a")
        self.assertIsNone(load_session_payload(first["session_id"], user_id="user-b"))

    @patch("src.chat.ask", return_value={"answer": "ok", "citations": [], "retrieved_sources": []})
    def test_paste_ark_session_id_links_thread(self, _ask) -> None:
        first = ask_in_session(
            None,
            "My session s-1j4dq5biie is stuck — help?",
            user_id="user-a",
        )
        from src.session_store import get_session_store

        stored = get_session_store().load(first["session_id"])
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.linked_ark_session_id, "s-1j4dq5biie")
        self.assertEqual(first.get("linked_ark_session_id"), "s-1j4dq5biie")

    @patch("src.chat.ask", return_value={"answer": "ok", "citations": [], "retrieved_sources": []})
    def test_list_user_sessions(self, _ask) -> None:
        ask_in_session(None, "first question", user_id="user-a")
        sessions = list_user_sessions("user-a")
        self.assertEqual(len(sessions), 1)
        self.assertIn("first question", sessions[0]["title"])


if __name__ == "__main__":
    unittest.main()
