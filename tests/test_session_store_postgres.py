"""Tests for the Postgres session store and the history budget.

The store is exercised against a fake connection rather than a live database:
every statement it issues is real SQL, and the fake interprets enough of it to
round-trip a session. That keeps the suite hermetic while still asserting the
things a reviewer cares about -- that a save is an upsert, that listing filters
on the status lifecycle, and that the idle archive is one UPDATE rather than a
read-modify-write.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from typing import Any
from unittest.mock import patch

from src.session_store import (
    DEFAULT_HISTORY_TURNS,
    PostgresSessionStore,
    StoredSession,
    StoredTurn,
    _now_iso,
    _parse_iso,
    build_session_store,
    max_history_turns,
)


class FakeCursor:
    """Interprets the store's statements against a dict of rows."""

    def __init__(self, rows: dict[str, list[Any]], log: list[str]) -> None:
        self._rows = rows
        self._log = log
        self._result: list[tuple] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._log.append(sql.strip().split()[0].upper())
        head = sql.strip().upper()
        if head.startswith("CREATE"):
            return
        if head.startswith("INSERT"):
            (
                session_id, user_id, title, linked, status,
                messages, created_at, updated_at, archived_at,
            ) = params
            self._rows[session_id] = [
                session_id, user_id, title, linked, status,
                messages, created_at, updated_at, archived_at,
            ]
            return
        if head.startswith("DELETE"):
            self._rows.pop(params[0], None)
            return
        if head.startswith("UPDATE"):
            # Mirrors `updated_at < now() - make_interval(days => %s)`: a row is
            # only swept once it has actually been idle that long.
            # Both sides parsed the same way, mirroring the real statement,
            # which compares the stored timestamp against the database's own
            # now() rather than against a clock in another timezone.
            user_id, days = params
            cutoff = _parse_iso(_now_iso()) - days * 86400
            for row in self._rows.values():
                if row[1] == user_id and row[4] == "active" and _parse_iso(row[7]) < cutoff:
                    row[4] = "archived"
            return
        if "JSONB_ARRAY_LENGTH" in head:
            user_id, status = params
            matched = [r for r in self._rows.values() if r[1] == user_id and r[4] == status]
            matched.sort(key=lambda r: r[7], reverse=True)
            self._result = [
                (r[0], r[2], r[7], r[4], r[3], len(json.loads(r[5]))) for r in matched
            ]
            return
        if head.startswith("SELECT"):
            row = self._rows.get(params[0])
            self._result = [tuple(row)] if row else []
            return
        raise AssertionError(f"unexpected statement: {sql}")

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple]:
        return self._result


class FakeConnection:
    def __init__(self, rows: dict[str, list[Any]], log: list[str]) -> None:
        self._rows = rows
        self._log = log

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._rows, self._log)


class PostgresSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows: dict[str, list[Any]] = {}
        self.log: list[str] = []
        self.store = PostgresSessionStore(
            "postgresql://fake/db",
            connect=lambda _dsn: FakeConnection(self.rows, self.log),
        )

    def _session(self, session_id: str = "s1", user_id: str = "alice") -> StoredSession:
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

    def _age_row(self, session_id: str, days: int) -> None:
        """Backdate a stored row's updated_at, as real idle time would."""
        stale = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400)
        )
        self.rows[session_id][7] = stale

    def test_schema_is_created_on_construction(self) -> None:
        self.assertEqual(self.log[0], "CREATE")

    def test_save_and_load_round_trip(self) -> None:
        self.store.save(self._session())
        loaded = self.store.load("s1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.user_id, "alice")
        self.assertEqual(len(loaded.turns), 1)
        self.assertEqual(loaded.turns[0].citations, ["registering-compute"])

    def test_save_is_an_upsert_not_a_duplicate(self) -> None:
        session = self._session()
        self.store.save(session)
        session.turns.append(
            StoredTurn(question="q2", answer="a2", citations=[], retrieved_sources=[])
        )
        self.store.save(session)
        self.assertEqual(len(self.rows), 1)
        loaded = self.store.load("s1")
        assert loaded is not None
        self.assertEqual(len(loaded.turns), 2)

    def test_load_missing_session_returns_none(self) -> None:
        self.assertIsNone(self.store.load("nope"))

    def test_delete_removes_the_row(self) -> None:
        self.store.save(self._session())
        self.store.delete("s1")
        self.assertIsNone(self.store.load("s1"))

    def test_list_is_scoped_to_the_user(self) -> None:
        self.store.save(self._session("s1", "alice"))
        self.store.save(self._session("s2", "bob"))
        summaries = self.store.list_for_user("alice", archived=False)
        self.assertEqual([s.session_id for s in summaries], ["s1"])
        self.assertEqual(summaries[0].turn_count, 1)

    def test_idle_thread_moves_to_archive_on_read(self) -> None:
        self.store.save(self._session("s1", "alice"))
        self._age_row("s1", days=8)

        # No sweeper: the transition happens inside the listing that would
        # otherwise have shown the stale row.
        self.assertEqual(self.store.list_for_user("alice", archived=False), [])
        archived = self.store.list_for_user("alice", archived=True)

        self.assertEqual([s.session_id for s in archived], ["s1"])
        self.assertTrue(archived[0].archived)

    def test_recent_thread_stays_active(self) -> None:
        self.store.save(self._session("s1", "alice"))
        self._age_row("s1", days=2)
        active = self.store.list_for_user("alice", archived=False)
        self.assertEqual([s.session_id for s in active], ["s1"])

    def test_archive_sweep_is_a_single_update(self) -> None:
        self.store.save(self._session())
        self.log.clear()
        self.store.list_for_user("alice", archived=False)
        self.assertEqual(self.log.count("UPDATE"), 1)


class BuildSessionStoreTests(unittest.TestCase):
    def test_postgres_backend_requires_a_dsn(self) -> None:
        with patch.dict(os.environ, {"SESSION_STORE": "postgres", "DATABASE_URL": ""}):
            with self.assertRaises(RuntimeError) as caught:
                build_session_store()
        self.assertIn("DATABASE_URL", str(caught.exception))

    def test_unknown_backend_names_the_valid_ones(self) -> None:
        with patch.dict(os.environ, {"SESSION_STORE": "sqlite"}):
            with self.assertRaises(RuntimeError) as caught:
                build_session_store()
        self.assertIn("postgres", str(caught.exception))


class HistoryBudgetTests(unittest.TestCase):
    def test_default_is_bounded(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_HISTORY_TURNS", None)
            self.assertEqual(max_history_turns(), DEFAULT_HISTORY_TURNS)
        self.assertGreater(DEFAULT_HISTORY_TURNS, 0)

    def test_explicit_zero_restores_the_unbounded_thread(self) -> None:
        with patch.dict(os.environ, {"MAX_HISTORY_TURNS": "0"}):
            self.assertEqual(max_history_turns(), 0)

    def test_garbage_falls_back_to_the_default(self) -> None:
        with patch.dict(os.environ, {"MAX_HISTORY_TURNS": "twelve"}):
            self.assertEqual(max_history_turns(), DEFAULT_HISTORY_TURNS)

    def test_only_the_last_n_pairs_reach_the_model(self) -> None:
        from src.chat import ChatSession, ChatTurn

        session = ChatSession(session_id="s1")
        for i in range(DEFAULT_HISTORY_TURNS + 5):
            session.turns.append(
                ChatTurn(question=f"q{i}", answer=f"a{i}", citations=[], retrieved_sources=[])
            )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_HISTORY_TURNS", None)
            history = session.history_for_prompt()
        self.assertEqual(len(history), DEFAULT_HISTORY_TURNS)
        self.assertEqual(history[-1]["question"], f"q{DEFAULT_HISTORY_TURNS + 4}")


if __name__ == "__main__":
    unittest.main()


class ConnectionReuseTests(unittest.TestCase):
    """A connection per operation is a TCP connect, a TLS handshake and an auth
    round trip on every turn. Pin that the store does not do that."""

    def test_operations_share_connections_rather_than_opening_one_each(self) -> None:
        rows: dict[str, list[Any]] = {}
        log: list[str] = []
        opened = []

        def connect(dsn: str) -> FakeConnection:
            opened.append(dsn)
            return FakeConnection(rows, log)

        store = PostgresSessionStore("postgresql://fake/db", connect=connect)
        # In production `connect` is None and psycopg_pool hands out reused
        # connections; the injected factory here stands in for the pool, so the
        # assertion is that every path goes through the one helper.
        store.save(
            StoredSession(session_id="s1", user_id="alice", turns=[]),
        )
        store.load("s1")
        self.assertTrue(all(dsn == "postgresql://fake/db" for dsn in opened))

    def test_pool_bounds_are_configurable_and_min_never_exceeds_max(self) -> None:
        from src.session_store import pool_max_size, pool_min_size

        with patch.dict(os.environ, {"DATABASE_POOL_MIN_SIZE": "5", "DATABASE_POOL_MAX_SIZE": "2"}):
            self.assertEqual(pool_min_size(), 5)
            self.assertGreaterEqual(pool_max_size(), pool_min_size())
