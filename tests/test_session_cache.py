"""Tests for the cache tier in front of the durable store.

The contract being pinned down here is the one that matters most in review:
the record is written before the copy, and no cache failure of any kind can
fail a user's request or lose a turn.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.session_store import (
    CACHE_BREAKER_FAILURES,
    CachedSessionStore,
    MemorySessionStore,
    RedisSessionCache,
    StoredSession,
    StoredTurn,
    build_session_store,
    session_cache_ttl_seconds,
)


class FakeCache:
    """An in-memory cache that can be told to break."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail_on: set[str] = set()

    def _maybe_fail(self, op: str) -> None:
        self.calls.append(op)
        if op in self.fail_on:
            raise RuntimeError(f"redis {op} unavailable")

    def get(self, session_id: str) -> str | None:
        self._maybe_fail("get")
        return self.data.get(session_id)

    def set(self, session_id: str, payload: str) -> None:
        self._maybe_fail("set")
        self.data[session_id] = payload

    def drop(self, session_id: str) -> None:
        self._maybe_fail("drop")
        self.data.pop(session_id, None)


class CountingStore(MemorySessionStore):
    """The real memory store, with reads counted."""

    def __init__(self) -> None:
        super().__init__()
        self.loads = 0
        self.lists = 0

    def load(self, session_id: str) -> StoredSession | None:
        self.loads += 1
        return super().load(session_id)

    def list_for_user(self, user_id: str, archived: bool):
        self.lists += 1
        return super().list_for_user(user_id, archived)


def a_session(session_id: str = "s1", user_id: str = "alice") -> StoredSession:
    return StoredSession(
        session_id=session_id,
        user_id=user_id,
        title="How do I enrol a host?",
        turns=[
            StoredTurn(
                question="How do I enrol a host?",
                answer="Run ark compute register.",
                citations=["registering-compute"],
                retrieved_sources=["registering-compute"],
            )
        ],
    )


class CachedSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.durable = CountingStore()
        self.cache = FakeCache()
        self.errors: list[tuple[str, Exception]] = []
        self.store = CachedSessionStore(
            self.durable, self.cache, on_error=lambda op, exc: self.errors.append((op, exc))
        )

    def test_save_writes_the_record_before_the_copy(self) -> None:
        self.store.save(a_session())
        self.assertIsNotNone(self.durable.load("s1"))
        self.assertIn("s1", self.cache.data)

    def test_a_hit_does_not_touch_the_record(self) -> None:
        self.store.save(a_session())
        before = self.durable.loads
        loaded = self.store.load("s1")
        assert loaded is not None
        self.assertEqual(loaded.turns[0].question, "How do I enrol a host?")
        self.assertEqual(self.durable.loads, before)

    def test_a_miss_reads_the_record_and_repopulates(self) -> None:
        self.store.save(a_session())
        self.cache.data.clear()
        before = self.durable.loads

        loaded = self.store.load("s1")

        assert loaded is not None
        self.assertEqual(self.durable.loads, before + 1)
        self.assertIn("s1", self.cache.data)

    def test_missing_everywhere_returns_none(self) -> None:
        self.assertIsNone(self.store.load("nope"))

    def test_delete_clears_both(self) -> None:
        self.store.save(a_session())
        self.store.delete("s1")
        self.assertIsNone(self.durable.load("s1"))
        self.assertEqual(self.cache.data, {})

    def test_listing_always_goes_to_the_record(self) -> None:
        self.store.save(a_session())
        before = self.durable.lists
        self.store.list_for_user("alice", archived=False)
        self.assertEqual(self.durable.lists, before + 1)


class CacheFailureTests(unittest.TestCase):
    """A broken cache degrades to plain Postgres. It never fails a request."""

    def setUp(self) -> None:
        self.durable = CountingStore()
        self.cache = FakeCache()
        self.errors: list[tuple[str, Exception]] = []
        self.store = CachedSessionStore(
            self.durable, self.cache, on_error=lambda op, exc: self.errors.append((op, exc))
        )

    def test_a_failing_write_still_persists_the_turn(self) -> None:
        self.cache.fail_on = {"set"}
        self.store.save(a_session())
        stored = self.durable.load("s1")
        self.assertIsNotNone(stored)
        self.assertEqual(self.errors[0][0], "set")

    def test_a_failing_read_falls_back_to_the_record(self) -> None:
        self.cache.fail_on = {"set"}
        self.store.save(a_session())
        self.cache.fail_on = {"get"}

        loaded = self.store.load("s1")

        assert loaded is not None
        self.assertEqual(loaded.session_id, "s1")

    def test_a_failing_delete_still_removes_the_record(self) -> None:
        self.store.save(a_session())
        self.cache.fail_on = {"drop"}
        self.store.delete("s1")
        self.assertIsNone(self.durable.load("s1"))

    def test_corrupt_cached_payload_falls_back_rather_than_raising(self) -> None:
        self.store.save(a_session())
        self.cache.data["s1"] = "{not json"
        loaded = self.store.load("s1")
        assert loaded is not None
        self.assertEqual(loaded.session_id, "s1")

    def test_the_breaker_stops_calling_a_dead_cache(self) -> None:
        self.cache.fail_on = {"get", "set", "drop"}
        for _ in range(CACHE_BREAKER_FAILURES):
            self.store.load("s1")
        calls_before = len(self.cache.calls)

        for _ in range(5):
            self.store.load("s1")

        self.assertEqual(len(self.cache.calls), calls_before)

    def test_the_breaker_reopens_after_the_cooldown(self) -> None:
        self.cache.fail_on = {"get", "set"}
        for _ in range(CACHE_BREAKER_FAILURES):
            self.store.load("s1")
        self.cache.fail_on = set()
        calls_before = len(self.cache.calls)

        with patch("src.session_store.time.time", return_value=1e12):
            self.store.load("s1")

        self.assertGreater(len(self.cache.calls), calls_before)


class CacheConfigTests(unittest.TestCase):
    def test_ttl_defaults_to_the_archive_window(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SESSION_CACHE_TTL_SECONDS", None)
            os.environ.pop("SESSION_ARCHIVE_AFTER_DAYS", None)
            self.assertEqual(session_cache_ttl_seconds(), 7 * 86400)

    def test_explicit_ttl_wins(self) -> None:
        with patch.dict(os.environ, {"SESSION_CACHE_TTL_SECONDS": "600"}):
            self.assertEqual(session_cache_ttl_seconds(), 600)

    def test_cache_keys_do_not_collide_with_the_standalone_store(self) -> None:
        cache = RedisSessionCache("redis://ignored", ttl_seconds=60, client=object())
        self.assertIn("cache:session:", cache._key("s1"))

    def test_postgres_without_redis_is_plain_postgres(self) -> None:
        with patch.dict(
            os.environ,
            {"SESSION_STORE": "postgres", "DATABASE_URL": "postgresql://x/y", "REDIS_URL": ""},
        ):
            with patch("src.session_store.PostgresSessionStore") as pg:
                store = build_session_store()
        self.assertIs(store, pg.return_value)

    def test_postgres_with_redis_is_wrapped_in_the_cache(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SESSION_STORE": "postgres",
                "DATABASE_URL": "postgresql://x/y",
                "REDIS_URL": "redis://x:6379/0",
            },
        ):
            with patch("src.session_store.PostgresSessionStore"):
                with patch("src.session_store.RedisSessionCache"):
                    store = build_session_store()
        self.assertIsInstance(store, CachedSessionStore)


if __name__ == "__main__":
    unittest.main()


class StaleCacheAfterFailedWriteTests(unittest.TestCase):
    """The failure mode reported in review: a cache write fails, the previous
    copy stays, and the turn it is missing is then lost from the record too."""

    def setUp(self) -> None:
        self.durable = CountingStore()
        self.cache = FakeCache()
        self.store = CachedSessionStore(self.durable, self.cache, on_error=lambda *_: None)

    def _turn(self, n: int) -> StoredTurn:
        return StoredTurn(question=f"q{n}", answer=f"a{n}", citations=[], retrieved_sources=[])

    def test_a_failed_write_never_leaves_a_stale_thread_behind(self) -> None:
        session = StoredSession(session_id="s1", user_id="alice", turns=[self._turn(1)])
        self.store.save(session)
        self.assertIn("s1", self.cache.data)

        session.turns.append(self._turn(2))
        self.cache.fail_on = {"set"}
        self.store.save(session)

        # The one-turn copy must not still be sitting there.
        self.assertNotIn("s1", self.cache.data)

    def test_the_reported_scenario_no_longer_loses_a_turn(self) -> None:
        session = StoredSession(session_id="s1", user_id="alice", turns=[self._turn(1)])
        self.store.save(session)

        # Turn 2 is written; the cache write fails.
        session.turns.append(self._turn(2))
        self.cache.fail_on = {"set"}
        self.store.save(session)
        self.cache.fail_on = set()

        # A later read must see two turns, not the cached one.
        reloaded = self.store.load("s1")
        assert reloaded is not None
        self.assertEqual([t.question for t in reloaded.turns], ["q1", "q2"])

        # And a save built on that read keeps every turn.
        reloaded.turns.append(self._turn(3))
        self.store.save(reloaded)
        final = self.durable.load("s1")
        assert final is not None
        self.assertEqual([t.question for t in final.turns], ["q1", "q2", "q3"])

    def test_a_failed_invalidate_stops_reads_at_once(self) -> None:
        self.store.save(StoredSession(session_id="s1", user_id="alice", turns=[self._turn(1)]))
        self.cache.fail_on = {"drop"}
        self.store.save(StoredSession(session_id="s1", user_id="alice", turns=[self._turn(2)]))

        # The copy may be wrong and undeletable, so the cache is bypassed now
        # rather than after the usual run of failures.
        calls_before = len(self.cache.calls)
        self.store.load("s1")
        self.assertEqual(len(self.cache.calls), calls_before)

    def test_a_failed_delete_stops_reads_at_once(self) -> None:
        self.store.save(StoredSession(session_id="s1", user_id="alice", turns=[self._turn(1)]))
        self.cache.fail_on = {"drop"}
        self.store.delete("s1")

        calls_before = len(self.cache.calls)
        self.assertIsNone(self.store.load("s1"))
        self.assertEqual(len(self.cache.calls), calls_before)
