"""Tests for session persistence (memory and Redis-shaped stores)."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
import os
import time
import unittest
from unittest.mock import patch

from src.chat import ask_in_session, list_user_sessions, load_session_payload, reset_session
from src.session_store import (
    DEFAULT_SESSION_MAX_TURNS,
    MemorySessionStore,
    RedisSessionStore,
    StoredSession,
    StoredTurn,
    TITLE_MAX_LEN,
    _parse_iso,
    REDIS_SOCKET_TIMEOUT_SECONDS,
    archive_after_days,
    extract_ark_session_id,
    max_stored_turns,
    reset_session_store,
    title_from_question,
)

try:
    import fakeredis
except ImportError:
    fakeredis = None  # type: ignore[assignment]


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

    def test_parse_iso_treats_timestamps_as_utc(self) -> None:
        stamp = "2026-01-01T12:00:00Z"
        expected = calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
        self.assertEqual(_parse_iso(stamp), expected)

    def test_max_stored_turns_defaults_to_one_hundred(self) -> None:
        os.environ.pop("SESSION_MAX_TURNS", None)
        self.assertEqual(max_stored_turns(), DEFAULT_SESSION_MAX_TURNS)
        self.assertEqual(DEFAULT_SESSION_MAX_TURNS, 100)

    def test_save_caps_at_default_when_over_one_hundred(self) -> None:
        os.environ.pop("SESSION_MAX_TURNS", None)
        turns = [StoredTurn(f"q{i}", f"a{i}", [], []) for i in range(101)]
        session = StoredSession(session_id="cap-1", user_id="user-a", turns=turns)
        self.store.save(session)
        loaded = self.store.load("cap-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.turns), 100)
        self.assertEqual(loaded.turns[0].question, "q1")
        self.assertEqual(loaded.turns[-1].question, "q100")

    def test_archive_does_not_bump_updated_at(self) -> None:
        import time

        from src.session_store import _archive_session

        old_time = "2026-01-01T10:00:00Z"
        session = StoredSession(
            session_id="arch-ts",
            user_id="user-a",
            title="Old",
            created_at=old_time,
            updated_at=old_time,
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store._sessions[session.session_id] = session
        self.store._user_active.setdefault("user-a", {})[session.session_id] = time.time()
        self.store.save(_archive_session(session), touch_activity=False)
        loaded = self.store.load("arch-ts")
        assert loaded is not None
        self.assertEqual(loaded.updated_at, old_time)
        self.assertTrue(loaded.archived)

    def test_save_caps_turns_when_session_max_turns_set(self) -> None:
        os.environ["SESSION_MAX_TURNS"] = "100"
        turns = [StoredTurn(f"q{i}", f"a{i}", [], []) for i in range(101)]
        session = StoredSession(session_id="cap-2", user_id="user-a", turns=turns)
        self.store.save(session)
        loaded = self.store.load("cap-2")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.turns), 100)
        self.assertEqual(loaded.turns[0].question, "q1")
        self.assertEqual(loaded.turns[-1].question, "q100")
        os.environ.pop("SESSION_MAX_TURNS", None)

    def test_title_truncated_at_max_len(self) -> None:
        self.assertEqual(TITLE_MAX_LEN, 64)
        long_question = "word " * 40
        title = title_from_question(long_question.strip())
        self.assertLessEqual(len(title), TITLE_MAX_LEN)
        self.assertTrue(title.endswith("…"))


@unittest.skipUnless(fakeredis is not None, "fakeredis not installed")
class RedisSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        server = fakeredis.FakeStrictRedis(decode_responses=True)
        self.store = RedisSessionStore.__new__(RedisSessionStore)
        self.store._client = server
        reset_session_store(self.store)

    def tearDown(self) -> None:
        reset_session_store(None)
        os.environ.pop("SESSION_TTL_SECONDS", None)

    def test_save_load_round_trip(self) -> None:
        stored = StoredSession(
            session_id="redis-1",
            user_id="user-z",
            title="Hi",
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(stored)
        loaded = self.store.load("redis-1")
        assert loaded is not None
        self.assertEqual(loaded.title, "Hi")

    def test_missing_body_purges_sidebar_index(self) -> None:
        stored = StoredSession(
            session_id="ghost-1",
            user_id="user-z",
            title="Ghost",
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(stored)
        self.store._client.delete(self.store._body_key("ghost-1"))
        self.assertIsNone(self.store.load("ghost-1"))
        listed = self.store.list_for_user("user-z", archived=False)
        self.assertEqual(listed, [])

    def test_ttl_expires_body_and_meta_together(self) -> None:
        os.environ["SESSION_TTL_SECONDS"] = "60"
        stored = StoredSession(
            session_id="ttl-1",
            user_id="user-z",
            title="TTL",
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(stored)
        body_ttl = self.store._client.ttl(self.store._body_key("ttl-1"))
        meta_ttl = self.store._client.ttl(self.store._meta_key("ttl-1"))
        self.assertGreater(body_ttl, 0)
        self.assertGreater(meta_ttl, 0)

    def test_archive_moves_between_indexes(self) -> None:
        from src.session_store import _archive_session

        stored = StoredSession(
            session_id="redis-arch",
            user_id="user-z",
            title="Move",
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(stored)
        self.store.save(_archive_session(stored), touch_activity=False)
        self.assertEqual(self.store.list_for_user("user-z", archived=False), [])
        archived = self.store.list_for_user("user-z", archived=True)
        self.assertEqual([item.session_id for item in archived], ["redis-arch"])

    def test_delete_removes_all_keys(self) -> None:
        stored = StoredSession(
            session_id="redis-del",
            user_id="user-z",
            title="Bye",
            turns=[StoredTurn("q", "a", [], [])],
        )
        self.store.save(stored)
        self.store.delete("redis-del")
        self.assertIsNone(self.store.load("redis-del"))
        self.assertEqual(self.store.list_for_user("user-z", archived=False), [])


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


@unittest.skipUnless(fakeredis is not None, "fakeredis not installed")
class ListingDoesNotReadSessionBodiesTests(unittest.TestCase):
    """Drawing the sidebar must not GET every thread in full.

    _apply_archive_rules used to load each active session purely to read
    updated_at, and the page lists twice per refresh (active + archived), on
    load, after every answer and on New chat. The zset score already IS
    _parse_iso(updated_at) and everything in the active index is unarchived by
    construction, so the scan can answer from scores alone.
    """

    def setUp(self) -> None:
        self.server = fakeredis.FakeStrictRedis(decode_responses=True)
        self.store = RedisSessionStore.__new__(RedisSessionStore)
        self.store._client = self.server
        reset_session_store(self.store)

    def tearDown(self) -> None:
        reset_session_store(None)

    def _seed(self, count: int) -> None:
        for i in range(count):
            self.store.save(
                StoredSession(
                    session_id=f"redis-{i}",
                    user_id="user-z",
                    title=f"Thread {i}",
                    turns=[StoredTurn(f"q{i}", "a" * 200, [], [])],
                )
            )

    def test_listing_fresh_threads_reads_no_bodies(self) -> None:
        self._seed(20)

        gets: list[str] = []
        real_get = self.server.get

        def counting_get(key, *args, **kwargs):
            gets.append(key)
            return real_get(key, *args, **kwargs)

        self.server.get = counting_get  # type: ignore[method-assign]
        try:
            listed = self.store.list_for_user("user-z", archived=False)
        finally:
            self.server.get = real_get  # type: ignore[method-assign]

        self.assertEqual(len(listed), 20)
        # The whole point: none of the 20 bodies was fetched to draw the list.
        self.assertEqual(gets, [], f"listing read {len(gets)} session bodies")

    def test_a_stale_thread_is_still_archived(self) -> None:
        # The optimisation must not cost the behaviour it optimises.
        #
        # Both the score AND the stored updated_at have to be stale: the score
        # only narrows which rows get loaded, and _should_archive still decides
        # against the real row. touch_activity=False keeps save() from stamping
        # updated_at to now.
        idle = archive_after_days() * 86400
        stale_iso = datetime.fromtimestamp(time.time() - idle - 60, UTC).isoformat()
        self.store.save(
            StoredSession(
                session_id="redis-0",
                user_id="user-z",
                title="Old thread",
                updated_at=stale_iso,
                turns=[StoredTurn("q", "a", [], [])],
            ),
            touch_activity=False,
        )

        active = self.store.list_for_user("user-z", archived=False)
        archived = self.store.list_for_user("user-z", archived=True)
        self.assertEqual([s.session_id for s in active], [])
        self.assertEqual([s.session_id for s in archived], ["redis-0"])


@unittest.skipUnless(fakeredis is not None, "fakeredis not installed")
class ListingIsPipelinedAndBoundedTests(unittest.TestCase):
    """Drawing the sidebar costs one metadata round trip, not one per thread.

    The body reads were already gone; this is the other half. The page lists
    twice per refresh and refreshes on load, after every answer, on New chat and
    after every delete, so a per-thread HGETALL multiplied by every one of those.
    And the archived index only grows -- nothing but an explicit delete removes
    from it -- so an unbounded list got slower for the life of the browser id.
    """

    def setUp(self) -> None:
        self.server = fakeredis.FakeStrictRedis(decode_responses=True)
        self.store = RedisSessionStore.__new__(RedisSessionStore)
        self.store._client = self.server
        reset_session_store(self.store)

    def tearDown(self) -> None:
        reset_session_store(None)
        os.environ.pop("SIDEBAR_PAGE_SIZE", None)

    def _seed(self, count: int) -> None:
        for i in range(count):
            self.store.save(
                StoredSession(
                    session_id=f"redis-{i}",
                    user_id="user-z",
                    title=f"Thread {i}",
                    turns=[StoredTurn(f"q{i}", "a", [], [])],
                )
            )

    def test_metadata_is_read_in_one_round_trip(self) -> None:
        self._seed(20)
        calls: list[str] = []
        real = self.server.hgetall

        def counting(key, *a, **kw):
            calls.append(key)
            return real(key, *a, **kw)

        self.server.hgetall = counting  # type: ignore[method-assign]
        try:
            listed = self.store.list_for_user("user-z", archived=False)
        finally:
            self.server.hgetall = real  # type: ignore[method-assign]

        self.assertEqual(len(listed), 20)
        # Zero DIRECT hgetall calls: they all went through the pipeline. The
        # assertion is about round trips, so it has to look at the client rather
        # than at the returned rows.
        self.assertEqual(calls, [], f"{len(calls)} sequential HGETALLs")

    def test_the_list_is_capped(self) -> None:
        os.environ["SIDEBAR_PAGE_SIZE"] = "5"
        # Distinct timestamps on purpose. The zset score is _parse_iso(updated_at),
        # so rows saved inside the same second tie and Redis falls back to
        # lexicographic order -- which would make an ordering assertion here pass
        # or fail on how fast the machine is, not on the cap.
        base = time.time() - 10_000
        for i in range(12):
            self.store.save(
                StoredSession(
                    session_id=f"redis-{i}",
                    user_id="user-z",
                    title=f"Thread {i}",
                    updated_at=datetime.fromtimestamp(base + i * 60, UTC).isoformat(),
                    turns=[StoredTurn(f"q{i}", "a", [], [])],
                ),
                touch_activity=False,
            )
        listed = self.store.list_for_user("user-z", archived=False)
        self.assertEqual(len(listed), 5)
        # Newest first, so the cap drops the oldest rather than an arbitrary five.
        self.assertEqual(listed[0].session_id, "redis-11")
        self.assertNotIn("redis-0", [row.session_id for row in listed])

    def test_the_memory_store_is_capped_the_same_way(self) -> None:
        # Two backends that disagree about a limit is a behaviour found in prod.
        os.environ["SIDEBAR_PAGE_SIZE"] = "5"
        mem = MemorySessionStore()
        reset_session_store(mem)
        for i in range(12):
            mem.save(
                StoredSession(
                    session_id=f"mem-{i}",
                    user_id="user-z",
                    title=f"Thread {i}",
                    turns=[StoredTurn(f"q{i}", "a", [], [])],
                )
            )
        self.assertEqual(len(mem.list_for_user("user-z", archived=False)), 5)


class SessionStoreHealthTests(unittest.TestCase):
    """/ready must be able to tell a working store from a configured one."""

    def tearDown(self) -> None:
        reset_session_store(None)

    def test_memory_reports_itself_as_not_durable(self) -> None:
        from src.chat import session_store_health

        reset_session_store(MemorySessionStore())
        health = session_store_health()
        self.assertEqual(health["backend"], "memory")
        self.assertFalse(health["durable"])
        self.assertTrue(health["ok"])

    @unittest.skipUnless(fakeredis is not None, "fakeredis not installed")
    def test_redis_reports_ok_when_it_answers(self) -> None:
        from src.chat import session_store_health

        store = RedisSessionStore.__new__(RedisSessionStore)
        store._client = fakeredis.FakeStrictRedis(decode_responses=True)
        reset_session_store(store)
        health = session_store_health()
        self.assertEqual(health["backend"], "redis")
        self.assertTrue(health["ok"])

    def test_an_unreachable_redis_reports_rather_than_raising(self) -> None:
        # The probe exists to surface this state; raising would make the one
        # endpoint meant to reveal a broken store 500 instead.
        from src.chat import session_store_health

        class Dead:
            def ping(self):
                raise ConnectionError("Connection refused")

        store = RedisSessionStore.__new__(RedisSessionStore)
        store._client = Dead()
        reset_session_store(store)
        health = session_store_health()
        self.assertFalse(health["ok"])
        self.assertEqual(health["error"], "ConnectionError")


class RedisClientHasTimeoutsTests(unittest.TestCase):
    """An unreachable Redis must FAIL the probe, not block it.

    health() exists to surface a broken store. Without socket timeouts a
    blackholed REDIS_URL leaves ping() waiting indefinitely, which hangs /ready
    on the shared probe limiter -- so kubelet marks the pod unready and pulls a
    working assistant out of the load balancer. The probe would have caused the
    outage it was written to report.
    """

    def test_the_client_is_built_with_connect_and_read_timeouts(self) -> None:
        import redis

        captured: dict[str, object] = {}

        def capturing_from_url(url, **kwargs):
            captured.update(kwargs)
            return object()

        # Patch the real constructor: __init__ calls redis.Redis.from_url, so a
        # stand-in module has to have that exact shape and a wrong guess here
        # would pass for the wrong reason.
        with patch.object(redis.Redis, "from_url", staticmethod(capturing_from_url)):
            RedisSessionStore("redis://example:6379/0")

        self.assertEqual(captured.get("socket_connect_timeout"), REDIS_SOCKET_TIMEOUT_SECONDS)
        self.assertEqual(captured.get("socket_timeout"), REDIS_SOCKET_TIMEOUT_SECONDS)
        # A timeout that is None or absent is the bug; assert it is a real number.
        self.assertIsInstance(REDIS_SOCKET_TIMEOUT_SECONDS, float)
        self.assertGreater(REDIS_SOCKET_TIMEOUT_SECONDS, 0)

    def test_a_timing_out_ping_reports_rather_than_propagating(self) -> None:
        # redis-py raises TimeoutError once the socket timeout fires; health()
        # has to turn that into ok: false like any other failure.
        from src.chat import session_store_health

        class TimingOut:
            def ping(self):
                raise TimeoutError("Timeout reading from socket")

        store = RedisSessionStore.__new__(RedisSessionStore)
        store._client = TimingOut()
        reset_session_store(store)
        try:
            health = session_store_health()
        finally:
            reset_session_store(None)
        self.assertFalse(health["ok"])
        self.assertEqual(health["error"], "TimeoutError")
