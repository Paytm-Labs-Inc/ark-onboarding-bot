"""Persist chat sessions.

Three backends, one Protocol:

- ``memory``   local dev only; dies with the process.
- ``postgres`` the store of record. Durable, and the shape Sina asked for.
- ``redis``    a cache in front of Postgres, or a standalone store for a
  deployment that has accepted the durability trade-off in writing.

Postgres is the default for anything that must survive a restart. Redis with
no TTL is not durable: an eviction policy may drop no-TTL keys under memory
pressure, and a restart without AOF or RDB loses the keyspace outright.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

_ARK_SESSION_ID_RE = re.compile(r"\bs-([a-z0-9]{8,})\b", re.IGNORECASE)

SESSION_KEY_PREFIX = "ark-onboarding-bot:"
DEFAULT_ARCHIVE_AFTER_DAYS = 7
TITLE_MAX_LEN = 80

# The history budget, in question/answer pairs sent to the model per ask.
#
# The model is stateless, so every ask resends the system prompt, the retrieved
# chunks and whatever history we choose. An uncapped thread therefore grows the
# prompt without bound: a 40-turn conversation at ~250 tokens a turn adds ~10k
# tokens to every subsequent ask, on top of the ~2.9k the retrieved chunks
# already cost. 12 pairs holds the working context a follow-up question needs
# while keeping the history contribution to roughly 3k tokens at worst.
#
# Older turns are dropped, not summarised. A rolling summary is the right next
# step and is deliberately not in this change: it needs a model call of its own
# and its own eval. Set MAX_HISTORY_TURNS=0 to restore the unbounded thread.
DEFAULT_HISTORY_TURNS = 12
SESSION_TABLE = "chat_sessions"


def max_stored_turns() -> int:
    """0 means unlimited."""
    raw = os.environ.get("SESSION_MAX_TURNS", "0").strip()
    if not raw or raw == "0":
        return 0
    try:
        return max(1, int(raw))
    except ValueError:
        return 0


def max_history_turns() -> int:
    """Turns sent to the model. 0 means the full stored thread (unbounded)."""
    raw = os.environ.get("MAX_HISTORY_TURNS", str(DEFAULT_HISTORY_TURNS)).strip()
    if not raw:
        return DEFAULT_HISTORY_TURNS
    if raw == "0":
        return 0
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_HISTORY_TURNS


def session_ttl_seconds() -> int | None:
    """Optional Redis TTL; unset means keep until explicit delete."""
    raw = os.environ.get("SESSION_TTL_SECONDS", "").strip()
    if not raw:
        return None
    try:
        return max(60, int(raw))
    except ValueError:
        return None


def archive_after_days() -> int:
    raw = os.environ.get("SESSION_ARCHIVE_AFTER_DAYS", str(DEFAULT_ARCHIVE_AFTER_DAYS)).strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_ARCHIVE_AFTER_DAYS
    except ValueError:
        return DEFAULT_ARCHIVE_AFTER_DAYS


def session_store_backend() -> str:
    return os.environ.get("SESSION_STORE", "memory").strip().lower() or "memory"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso(value: str) -> float:
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def title_from_question(question: str) -> str:
    text = " ".join(question.split())
    if len(text) <= TITLE_MAX_LEN:
        return text
    return text[: TITLE_MAX_LEN - 1] + "…"


def extract_ark_session_id(text: str) -> str | None:
    """Return the first Ark session id (e.g. s-1j4dq5biie) found in text."""
    match = _ARK_SESSION_ID_RE.search(text)
    if not match:
        return None
    return f"s-{match.group(1).lower()}"


@dataclass
class StoredTurn:
    question: str
    answer: str
    citations: list[str]
    retrieved_sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": self.citations,
            "retrieved_sources": self.retrieved_sources,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredTurn:
        return cls(
            question=str(data.get("question", "")),
            answer=str(data.get("answer", "")),
            citations=[str(x) for x in data.get("citations", [])],
            retrieved_sources=[str(x) for x in data.get("retrieved_sources", [])],
        )


@dataclass
class StoredSession:
    session_id: str
    user_id: str = "anonymous"
    title: str = ""
    linked_ark_session_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    archived_at: str | None = None
    turns: list[StoredTurn] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived": self.archived,
            "archived_at": self.archived_at,
            "turns": [turn.to_dict() for turn in self.turns],
        }
        if self.linked_ark_session_id:
            payload["linked_ark_session_id"] = self.linked_ark_session_id
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredSession | None:
        if not isinstance(data, dict):
            return None
        session_id = str(data.get("session_id") or "")
        if not session_id:
            return None
        turns_raw = data.get("turns")
        if not isinstance(turns_raw, list):
            return None
        turns = [StoredTurn.from_dict(item) for item in turns_raw if isinstance(item, dict)]
        archived_at = data.get("archived_at")
        linked = data.get("linked_ark_session_id")
        return cls(
            session_id=session_id,
            user_id=str(data.get("user_id") or "anonymous"),
            title=str(data.get("title") or ""),
            linked_ark_session_id=str(linked).lower() if linked else None,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            archived=bool(data.get("archived")),
            archived_at=str(archived_at) if archived_at else None,
            turns=turns,
        )

    @classmethod
    def from_json(cls, raw: str) -> StoredSession | None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return cls.from_dict(data)


@dataclass
class SessionSummary:
    session_id: str
    title: str
    updated_at: str
    turn_count: int
    archived: bool
    linked_ark_session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
            "archived": self.archived,
        }
        if self.linked_ark_session_id:
            payload["linked_ark_session_id"] = self.linked_ark_session_id
        return payload


def _cap_turns(turns: list[StoredTurn]) -> list[StoredTurn]:
    cap = max_stored_turns()
    if cap <= 0 or len(turns) <= cap:
        return list(turns)
    return turns[-cap:]


class SessionStore(Protocol):
    def load(self, session_id: str) -> StoredSession | None: ...

    def save(self, session: StoredSession) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]: ...


def _should_archive(session: StoredSession, idle_days: int) -> bool:
    if session.archived:
        return False
    updated = _parse_iso(session.updated_at)
    if updated <= 0:
        return False
    return (time.time() - updated) >= idle_days * 86400


def _archive_session(session: StoredSession) -> StoredSession:
    session.archived = True
    session.archived_at = _now_iso()
    return session


class MemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, StoredSession] = {}
        self._user_active: dict[str, dict[str, float]] = {}
        self._user_archived: dict[str, dict[str, float]] = {}

    def load(self, session_id: str) -> StoredSession | None:
        return self._sessions.get(session_id)

    def save(self, session: StoredSession) -> None:
        session.turns = _cap_turns(session.turns)
        if not session.created_at:
            session.created_at = _now_iso()
        session.updated_at = _now_iso()
        if session.archived:
            session.archived_at = session.archived_at or _now_iso()
        else:
            session.archived_at = None
        self._sessions[session.session_id] = session
        score = _parse_iso(session.updated_at) or time.time()
        user = session.user_id
        if session.archived:
            self._user_active.setdefault(user, {}).pop(session.session_id, None)
            self._user_archived.setdefault(user, {})[session.session_id] = (
                _parse_iso(session.archived_at or session.updated_at) or score
            )
        else:
            self._user_archived.setdefault(user, {}).pop(session.session_id, None)
            self._user_active.setdefault(user, {})[session.session_id] = score

    def delete(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        user = session.user_id
        self._user_active.get(user, {}).pop(session_id, None)
        self._user_archived.get(user, {}).pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()
        self._user_active.clear()
        self._user_archived.clear()

    def _apply_archive_rules(self, user_id: str) -> None:
        idle_days = archive_after_days()
        for session_id in list(self._user_active.get(user_id, {})):
            session = self._sessions.get(session_id)
            if session is None or not _should_archive(session, idle_days):
                continue
            self.save(_archive_session(session))

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]:
        self._apply_archive_rules(user_id)
        index = self._user_archived if archived else self._user_active
        ordered = sorted(
            index.get(user_id, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )
        summaries: list[SessionSummary] = []
        for session_id, _score in ordered:
            session = self._sessions.get(session_id)
            if session is None:
                continue
            summaries.append(
                SessionSummary(
                    session_id=session.session_id,
                    title=session.title or "Untitled chat",
                    updated_at=session.updated_at,
                    turn_count=len(session.turns),
                    archived=session.archived,
                    linked_ark_session_id=session.linked_ark_session_id,
                )
            )
        return summaries


class RedisSessionStore:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)

    def _body_key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}session:{session_id}"

    def _meta_key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}session:meta:{session_id}"

    def _active_key(self, user_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}user:{user_id}:active"

    def _archived_key(self, user_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}user:{user_id}:archived"

    def load(self, session_id: str) -> StoredSession | None:
        raw = self._client.get(self._body_key(session_id))
        if not raw:
            return None
        return StoredSession.from_json(raw)

    def save(self, session: StoredSession) -> None:
        session.turns = _cap_turns(session.turns)
        if not session.created_at:
            session.created_at = _now_iso()
        session.updated_at = _now_iso()
        if session.archived:
            session.archived_at = session.archived_at or _now_iso()
        else:
            session.archived_at = None

        body_key = self._body_key(session.session_id)
        payload = session.to_json()
        ttl = session_ttl_seconds()
        if ttl is not None:
            self._client.setex(body_key, ttl, payload)
        else:
            self._client.set(body_key, payload)

        meta = {
            "user_id": session.user_id,
            "title": session.title,
            "linked_ark_session_id": session.linked_ark_session_id or "",
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "archived": "1" if session.archived else "0",
            "archived_at": session.archived_at or "",
            "turn_count": str(len(session.turns)),
        }
        self._client.hset(self._meta_key(session.session_id), mapping=meta)

        user = session.user_id
        if session.archived:
            score = _parse_iso(session.archived_at or session.updated_at) or time.time()
            self._client.zrem(self._active_key(user), session.session_id)
            self._client.zadd(self._archived_key(user), {session.session_id: score})
        else:
            score = _parse_iso(session.updated_at) or time.time()
            self._client.zrem(self._archived_key(user), session.session_id)
            self._client.zadd(self._active_key(user), {session.session_id: score})

    def delete(self, session_id: str) -> None:
        meta = self._client.hgetall(self._meta_key(session_id))
        user_id = meta.get("user_id", "")
        self._client.delete(self._body_key(session_id), self._meta_key(session_id))
        if user_id:
            self._client.zrem(self._active_key(user_id), session_id)
            self._client.zrem(self._archived_key(user_id), session_id)

    def _apply_archive_rules(self, user_id: str) -> None:
        idle_days = archive_after_days()
        active_ids = self._client.zrange(self._active_key(user_id), 0, -1)
        for session_id in active_ids:
            session = self.load(session_id)
            if session is None or not _should_archive(session, idle_days):
                continue
            self.save(_archive_session(session))

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]:
        self._apply_archive_rules(user_id)
        zkey = self._archived_key(user_id) if archived else self._active_key(user_id)
        session_ids = self._client.zrevrange(zkey, 0, -1)
        summaries: list[SessionSummary] = []
        for session_id in session_ids:
            meta = self._client.hgetall(self._meta_key(session_id))
            if not meta:
                session = self.load(session_id)
                if session is None:
                    continue
                meta = {
                    "title": session.title,
                    "updated_at": session.updated_at,
                    "turn_count": str(len(session.turns)),
                    "archived": "1" if session.archived else "0",
                    "linked_ark_session_id": session.linked_ark_session_id or "",
                }
            linked = meta.get("linked_ark_session_id") or ""
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    title=meta.get("title") or "Untitled chat",
                    updated_at=meta.get("updated_at") or "",
                    turn_count=int(meta.get("turn_count") or "0"),
                    archived=meta.get("archived") == "1",
                    linked_ark_session_id=linked.lower() if linked else None,
                )
            )
        return summaries


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {SESSION_TABLE} (
  session_id            TEXT PRIMARY KEY,
  user_id               TEXT NOT NULL DEFAULT 'anonymous',
  title                 TEXT NOT NULL DEFAULT '',
  linked_ark_session_id TEXT,
  status                TEXT NOT NULL DEFAULT 'active',
  messages              JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_{SESSION_TABLE}_user
  ON {SESSION_TABLE} (user_id, status, updated_at DESC);
"""


class PostgresSessionStore:
    """The store of record.

    Schema is deliberately the shape of foundry-platform migration 051
    (``assistant_sessions``): an id, the owning user, a status lifecycle and
    the turns in a JSONB column. Sina pointed at that table and the decision
    was to copy the pattern rather than write into Ark's control-plane
    database, so our migrations stay ours.

    One row per session. The turn list is a JSONB document because it is
    always read and written whole, which is exactly the access pattern the
    Scout table was built for.
    """

    def __init__(self, dsn: str, connect: Any | None = None) -> None:
        self._dsn = dsn
        if connect is None:
            import psycopg

            connect = psycopg.connect
        self._connect = connect
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)

    def load(self, session_id: str) -> StoredSession | None:
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT session_id, user_id, title, linked_ark_session_id, status,"
                    f" messages, created_at, updated_at, archived_at"
                    f" FROM {SESSION_TABLE} WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
        return _row_to_session(row)

    def save(self, session: StoredSession) -> None:
        session.turns = _cap_turns(session.turns)
        if not session.created_at:
            session.created_at = _now_iso()
        session.updated_at = _now_iso()
        if session.archived:
            session.archived_at = session.archived_at or _now_iso()
        else:
            session.archived_at = None

        messages = json.dumps([turn.to_dict() for turn in session.turns], ensure_ascii=False)
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {SESSION_TABLE}"
                    f" (session_id, user_id, title, linked_ark_session_id, status,"
                    f"  messages, created_at, updated_at, archived_at)"
                    f" VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
                    f" ON CONFLICT (session_id) DO UPDATE SET"
                    f"  user_id = EXCLUDED.user_id,"
                    f"  title = EXCLUDED.title,"
                    f"  linked_ark_session_id = EXCLUDED.linked_ark_session_id,"
                    f"  status = EXCLUDED.status,"
                    f"  messages = EXCLUDED.messages,"
                    f"  updated_at = EXCLUDED.updated_at,"
                    f"  archived_at = EXCLUDED.archived_at",
                    (
                        session.session_id,
                        session.user_id,
                        session.title,
                        session.linked_ark_session_id,
                        "archived" if session.archived else "active",
                        messages,
                        session.created_at,
                        session.updated_at,
                        session.archived_at,
                    ),
                )

    def delete(self, session_id: str) -> None:
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {SESSION_TABLE} WHERE session_id = %s",
                    (session_id,),
                )

    def _apply_archive_rules(self, user_id: str) -> None:
        """Lazy-on-read archive, in one statement.

        Bhurva's question was sweeper job or lazy-on-read. This is lazy: the
        transition happens in the same round trip as the list that would have
        shown the stale row, so there is no cron to own and no window where a
        listing and the archive state disagree.
        """
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {SESSION_TABLE}"
                    f" SET status = 'archived', archived_at = now()"
                    f" WHERE user_id = %s AND status = 'active'"
                    f"   AND updated_at < now() - make_interval(days => %s)",
                    (user_id, archive_after_days()),
                )

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]:
        self._apply_archive_rules(user_id)
        status = "archived" if archived else "active"
        with self._connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT session_id, title, updated_at, status, linked_ark_session_id,"
                    f" jsonb_array_length(messages)"
                    f" FROM {SESSION_TABLE}"
                    f" WHERE user_id = %s AND status = %s"
                    f" ORDER BY updated_at DESC",
                    (user_id, status),
                )
                rows = cur.fetchall() or []
        summaries: list[SessionSummary] = []
        for row in rows:
            linked = row[4] or ""
            summaries.append(
                SessionSummary(
                    session_id=str(row[0]),
                    title=str(row[1] or "") or "Untitled chat",
                    updated_at=_as_iso(row[2]),
                    turn_count=int(row[5] or 0),
                    archived=str(row[3]) == "archived",
                    linked_ark_session_id=linked.lower() if linked else None,
                )
            )
        return summaries


def _as_iso(value: Any) -> str:
    """Render a timestamp column as the ISO string the API contract uses."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    strftime = getattr(value, "strftime", None)
    if strftime is None:
        return str(value)
    return strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_session(row: Any) -> StoredSession | None:
    if not row:
        return None
    messages = row[5]
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except json.JSONDecodeError:
            messages = []
    if not isinstance(messages, list):
        messages = []
    linked = row[3] or ""
    return StoredSession(
        session_id=str(row[0]),
        user_id=str(row[1] or "anonymous"),
        title=str(row[2] or ""),
        linked_ark_session_id=linked.lower() if linked else None,
        created_at=_as_iso(row[6]),
        updated_at=_as_iso(row[7]),
        archived=str(row[4]) == "archived",
        archived_at=_as_iso(row[8]) or None,
        turns=[StoredTurn.from_dict(item) for item in messages if isinstance(item, dict)],
    )


_store: SessionStore | None = None


def build_session_store() -> SessionStore:
    backend = session_store_backend()
    if backend == "memory":
        return MemorySessionStore()
    if backend == "postgres":
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            raise RuntimeError("SESSION_STORE=postgres requires DATABASE_URL to be set.")
        return PostgresSessionStore(dsn)
    if backend == "redis":
        url = os.environ.get("REDIS_URL", "").strip()
        if not url:
            raise RuntimeError("SESSION_STORE=redis requires REDIS_URL to be set.")
        return RedisSessionStore(url)
    raise RuntimeError(
        f"Unknown SESSION_STORE {backend!r}; use 'memory', 'postgres' or 'redis'."
    )


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = build_session_store()
    return _store


def reset_session_store(for_tests: SessionStore | None = None) -> None:
    global _store
    _store = for_tests if for_tests is not None else build_session_store()


def stored_to_payload(stored: StoredSession) -> dict[str, Any]:
    payload = stored.to_dict()
    payload.pop("user_id", None)
    return payload


def stored_to_summary(stored: StoredSession) -> SessionSummary:
    return SessionSummary(
        session_id=stored.session_id,
        title=stored.title or "Untitled chat",
        updated_at=stored.updated_at,
        turn_count=len(stored.turns),
        archived=stored.archived,
        linked_ark_session_id=stored.linked_ark_session_id,
    )
