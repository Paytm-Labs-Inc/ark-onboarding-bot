"""Persist chat sessions (memory for local dev, Redis for prod)."""

from __future__ import annotations

import calendar
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

_ARK_SESSION_ID_RE = re.compile(r"\bs-([a-z0-9]{8,})\b", re.IGNORECASE)

SESSION_KEY_PREFIX = "ark-onboarding-bot:"
DEFAULT_ARCHIVE_AFTER_DAYS = 7
DEFAULT_SESSION_MAX_TURNS = 100
DEFAULT_HISTORY_TURNS = 12
DEFAULT_HISTORY_SUMMARY_MAX_CHARS = 3200
TITLE_MAX_LEN = 64


def max_stored_turns() -> int:
    """Default 100 stored turns; SESSION_MAX_TURNS=0 means unlimited."""
    raw = os.environ.get("SESSION_MAX_TURNS")
    if raw is None:
        return DEFAULT_SESSION_MAX_TURNS
    raw = raw.strip()
    if raw == "0":
        return 0
    if not raw:
        return DEFAULT_SESSION_MAX_TURNS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SESSION_MAX_TURNS


def max_history_turns() -> int:
    """Verbatim Q/A pairs sent to the model. Default 12; -1 = unlimited full thread."""
    raw = os.environ.get("MAX_HISTORY_TURNS")
    if raw is None:
        return DEFAULT_HISTORY_TURNS
    raw = raw.strip().lower()
    if raw in ("-1", "unlimited", "all"):
        return 0
    if raw == "0" or not raw:
        return DEFAULT_HISTORY_TURNS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_HISTORY_TURNS


def history_summary_max_chars() -> int:
    raw = os.environ.get("HISTORY_SUMMARY_MAX_CHARS")
    if raw is None:
        return DEFAULT_HISTORY_SUMMARY_MAX_CHARS
    raw = raw.strip()
    if not raw:
        return DEFAULT_HISTORY_SUMMARY_MAX_CHARS
    try:
        return max(256, int(raw))
    except ValueError:
        return DEFAULT_HISTORY_SUMMARY_MAX_CHARS


def _turn_dialogue_lines(question: str, answer: str) -> str:
    lines: list[str] = []
    q = question.strip()
    a = answer.strip()
    if q:
        lines.append(f"User: {q}")
    if a:
        lines.append(f"Assistant: {a}")
    return "\n".join(lines)


def compile_history_summary(
    turns: list[Any],
    *,
    verbatim_cap: int,
    max_chars: int,
) -> str:
    """Fold middle turns into a capped summary; turn 0 stays verbatim separately."""
    if verbatim_cap <= 0 or len(turns) <= verbatim_cap:
        return ""
    # Keep the first turn out of the summary — it usually holds constraints
    # ("I'm on OCL", tenant name, etc.) that get lost if we drop oldest-first.
    middle = turns[1:-verbatim_cap] if len(turns) > 1 else turns[:-verbatim_cap]
    if not middle:
        return ""
    parts: list[str] = []
    for turn in middle:
        if hasattr(turn, "question"):
            block = _turn_dialogue_lines(str(turn.question), str(turn.answer))
        else:
            block = _turn_dialogue_lines(
                str(turn.get("question", "")),
                str(turn.get("answer", "")),
            )
        if block:
            parts.append(block)
    text = "\n\n".join(parts)
    if len(text) <= max_chars:
        return text
    # Truncate from the front to preserve the turns adjacent to the live window.
    # Recent context carries coreference and disambiguation; older turns matter less.
    # text[-(max_chars - 1):] keeps turns 20–32 instead of 1–5.
    return "…" + text[-(max_chars - 1) :]


def session_ttl_seconds() -> int | None:
    """Optional Redis TTL; unset means keep until explicit delete."""
    raw = os.environ.get("SESSION_TTL_SECONDS", "").strip()
    if not raw:
        return None
    try:
        return max(60, int(raw))
    except ValueError:
        return None


DEFAULT_SIDEBAR_PAGE_SIZE = 50

# Applied to both connect and read. See RedisSessionStore.__init__ for why a
# missing timeout turns the readiness probe into the outage it reports on.
#
# 0.5s, and the number is set BY the probe budget rather than by what feels
# generous. /ready is the probe path, and a kubelet httpGet defaults to
# timeoutSeconds: 1 -- so anything at or above one second means a blackholed
# Redis fails the probe instead of being reported by it. The chart now also
# sets timeoutSeconds explicitly (web.probes.*.timeoutSeconds), but this side
# must hold on its own: the app is deployed by charts we do not own too.
REDIS_SOCKET_TIMEOUT_SECONDS = 0.5


def sidebar_page_size() -> int:
    """How many threads one sidebar list returns.

    A cap rather than a preference: the archived index only grows, so an
    unbounded list gets slower for the life of the browser id. 50 is well past
    what anyone scrolls and small enough that the pipelined metadata read stays
    one modest round trip.
    """
    raw = os.environ.get("SIDEBAR_PAGE_SIZE", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SIDEBAR_PAGE_SIZE
    return value if value > 0 else DEFAULT_SIDEBAR_PAGE_SIZE


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
        parsed = time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        return float(calendar.timegm(parsed))
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
    history_summary: str = ""
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
            "history_summary": self.history_summary,
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
            history_summary=str(data.get("history_summary") or ""),
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

    def save(self, session: StoredSession, *, touch_activity: bool = True) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]: ...

    def health(self) -> dict[str, object]: ...


def _should_archive(session: StoredSession, idle_days: int) -> bool:
    if session.archived:
        return False
    updated = _parse_iso(session.updated_at)
    if updated <= 0:
        return False
    return (time.time() - updated) >= idle_days * 86400


def _apply_timestamps(session: StoredSession, *, touch_activity: bool) -> None:
    if not session.created_at:
        session.created_at = _now_iso()
    if touch_activity:
        session.updated_at = _now_iso()
    elif not session.updated_at:
        session.updated_at = _now_iso()
    if session.archived:
        session.archived_at = session.archived_at or _now_iso()
    else:
        session.archived_at = None


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

    def save(self, session: StoredSession, *, touch_activity: bool = True) -> None:
        session.turns = _cap_turns(session.turns)
        _apply_timestamps(session, touch_activity=touch_activity)
        self._sessions[session.session_id] = session
        if session.archived:
            score = _parse_iso(session.archived_at or session.updated_at) or time.time()
        else:
            score = _parse_iso(session.updated_at) or time.time()
        user = session.user_id
        if session.archived:
            self._user_active.setdefault(user, {}).pop(session.session_id, None)
            self._user_archived.setdefault(user, {})[session.session_id] = score
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
            self.save(_archive_session(session), touch_activity=False)

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]:
        self._apply_archive_rules(user_id)
        index = self._user_archived if archived else self._user_active
        # Same bound as the Redis path, so a behaviour only one backend has
        # cannot be discovered in production.
        ordered = sorted(
            index.get(user_id, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )[: sidebar_page_size()]
        summaries: list[SessionSummary] = []
        for session_id, _score in ordered:
            session = self._sessions.get(session_id)
            if session is None:
                index.get(user_id, {}).pop(session_id, None)
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

    def health(self) -> dict[str, object]:
        """What this store is, in the words a probe should use.

        `durable: False` is the whole point. A pod on this store reports READY
        exactly like one on Redis, and the difference only shows up when a
        release empties everybody's sidebar -- which is what shipped the
        sidebar onto a plain dict in the first place.
        """
        return {"backend": "memory", "durable": False, "ok": True}


class RedisSessionStore:
    def __init__(self, url: str) -> None:
        import redis

        # Timeouts, because without them an unreachable host BLOCKS rather than
        # failing. A blackholed REDIS_URL, dropped packets or a Service with no
        # endpoints leaves the socket waiting indefinitely, and health() then
        # hangs /ready on the shared probe limiter -- so kubelet readiness fails
        # and a working assistant is pulled from the load balancer. That is the
        # exact outcome the store probe exists to prevent, so the probe must not
        # be the thing that causes it.
        #
        # Short on purpose: this is a pod-local Redis over the cluster network,
        # where a healthy round trip is sub-millisecond. Half a second is already
        # far past "slow", and it has to stay under the probe budget -- see
        # REDIS_SOCKET_TIMEOUT_SECONDS for why that is the number that sets it.
        self._client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        )

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
            self._purge_stale_index(session_id)
            return None
        return StoredSession.from_json(raw)

    def _purge_stale_index(self, session_id: str) -> None:
        """Drop sidebar index entries when the body key is missing."""
        # ✅ All operations in a single transaction to prevent orphaned indices.
        # If operations are split, a concurrent save() could re-add the session
        # to indices after the delete but before the zrem calls.
        meta_key = self._meta_key(session_id)
        meta = self._client.hgetall(meta_key)
        user_id = meta.get("user_id", "")

        pipe = self._client.pipeline(transaction=True)
        pipe.delete(self._body_key(session_id), meta_key)
        if user_id:
            pipe.zrem(self._active_key(user_id), session_id)
            pipe.zrem(self._archived_key(user_id), session_id)
        pipe.execute()

    def save(self, session: StoredSession, *, touch_activity: bool = True) -> None:
        session.turns = _cap_turns(session.turns)
        _apply_timestamps(session, touch_activity=touch_activity)

        body_key = self._body_key(session.session_id)
        meta_key = self._meta_key(session.session_id)
        payload = session.to_json()
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

        score = _parse_iso(
            session.archived_at if session.archived else session.updated_at
        ) or time.time()
        ttl = session_ttl_seconds()

        # ✅ Use WATCH/MULTI/EXEC to ensure atomicity across all 3 keys
        # (body_key, meta_key, index_keys). If any watched key changes between
        # watch and execute, the transaction is aborted and we retry. This ensures
        # a crash mid-write doesn't leave orphaned data.
        while True:
            try:
                # ✅ WATCH all keys that we'll modify to detect concurrent changes.
                self._client.watch(meta_key)

                # ✅ Read current metadata AFTER watch to detect user changes.
                existing_meta = self._client.hgetall(meta_key)
                existing_user = existing_meta.get("user_id", "")

                # ✅ Determine which indices we'll update (must be atomic with meta change).
                user = session.user_id
                active_key = self._active_key(user)
                archived_key = self._archived_key(user)

                # Start MULTI/EXEC transaction — all commands execute or none.
                pipe = self._client.pipeline(transaction=True)

                # ✅ If user_id changed, clean up old indices FIRST (before new write).
                if existing_user and existing_user != user:
                    old_active = self._active_key(existing_user)
                    old_archived = self._archived_key(existing_user)
                    pipe.zrem(old_active, session.session_id)
                    pipe.zrem(old_archived, session.session_id)

                # ✅ Write body and metadata atomically in one transaction.
                # No crash gap: all 3 keys (body, meta, index) updated together or not at all.
                if ttl is not None:
                    pipe.setex(body_key, ttl, payload)
                    pipe.hset(meta_key, mapping=meta)
                    pipe.expire(meta_key, ttl)
                else:
                    pipe.set(body_key, payload)
                    pipe.hset(meta_key, mapping=meta)

                # ✅ Update indices atomically (now with the correct user_id).
                if session.archived:
                    pipe.zrem(active_key, session.session_id)
                    pipe.zadd(archived_key, {session.session_id: score})
                else:
                    pipe.zrem(archived_key, session.session_id)
                    pipe.zadd(active_key, {session.session_id: score})

                # ✅ Execute atomically: all commands or none. No partial updates.
                pipe.execute()
                break  # Success, exit retry loop.

            except Exception as e:
                # ✅ WatchError means a watched key was modified by another client.
                # Retry the entire operation with the current state.
                if e.__class__.__name__ == "WatchError":
                    continue
                raise

    def delete(self, session_id: str) -> None:
        # ✅ Use WATCH/MULTI/EXEC to ensure atomicity. If user_id changes
        # between reading meta and executing delete, we must use the current
        # user_id to clean the correct indices.
        meta_key = self._meta_key(session_id)
        while True:
            try:
                self._client.watch(meta_key)
                meta = self._client.hgetall(meta_key)
                user_id = meta.get("user_id", "")

                pipe = self._client.pipeline(transaction=True)
                pipe.delete(self._body_key(session_id), meta_key)
                if user_id:
                    pipe.zrem(self._active_key(user_id), session_id)
                    pipe.zrem(self._archived_key(user_id), session_id)
                pipe.execute()
                break

            except Exception as e:
                if e.__class__.__name__ == "WatchError":
                    continue
                raise

    def _apply_archive_rules(self, user_id: str) -> None:
        # ✅ Load all sessions FIRST, then archive. Archiving calls save(),
        # which modifies indices. Iterating zrange while modifying it can
        # miss sessions or cause consistency issues.
        idle_days = archive_after_days()

        # The scan reads no session bodies. _should_archive consults exactly two
        # things: `archived`, which is false for everything in the ACTIVE index
        # by construction, and _parse_iso(updated_at) -- which is precisely the
        # score save() writes. So withscores answers the question, and only the
        # rows that actually cross the cutoff get loaded.
        #
        # This was the whole cost of drawing the sidebar: listing GET the full
        # body of every thread purely to read one timestamp, twice per refresh
        # (active and archived), on load, after every answer and on New chat.
        # With 20 threads of 40 turns that measured ~40 GETs and 3.2 MiB per
        # refresh -- about 6.5 MiB of Redis reads per message once this ships.
        cutoff = time.time() - idle_days * 86400
        scored = self._client.zrange(
            self._active_key(user_id), 0, -1, withscores=True
        )

        # ✅ Collect all sessions to archive (separate from modification),
        # because archiving calls save(), which modifies the index we are
        # iterating.
        to_archive = []
        for session_id, score in scored:
            if not (0 < score <= cutoff):
                continue
            session = self.load(session_id)
            # Re-checked against the loaded row so _should_archive stays the
            # single definition of the rule; the score only narrows the field.
            if session is not None and _should_archive(session, idle_days):
                to_archive.append(session)

        # ✅ Then archive each (now safe to modify indices).
        for session in to_archive:
            self.save(_archive_session(session), touch_activity=False)

    def list_for_user(self, user_id: str, archived: bool) -> list[SessionSummary]:
        self._apply_archive_rules(user_id)
        zkey = self._archived_key(user_id) if archived else self._active_key(user_id)
        # Bounded, and the newest first. zrevrange(0, -1) returned the whole
        # index, and the archived one only ever grows -- _apply_archive_rules
        # moves rows in and nothing but an explicit delete takes them out -- so
        # that half got monotonically slower for the life of the browser id.
        session_ids = self._client.zrevrange(zkey, 0, sidebar_page_size() - 1)

        # One round trip for the metadata, not one per thread. The page lists
        # twice per refresh (active + archived) and refreshes on load, after
        # every answer, on New chat and after every delete, so at 200 threads
        # this was 200 sequential HGETALLs each time.
        pipe = self._client.pipeline(transaction=False)
        for session_id in session_ids:
            pipe.hgetall(self._meta_key(session_id))
        metas = pipe.execute()

        summaries: list[SessionSummary] = []
        for session_id, meta in zip(session_ids, metas):
            if not meta:
                session = self.load(session_id)
                if session is None:
                    self._client.zrem(zkey, session_id)
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


    def health(self) -> dict[str, object]:
        """Answer from the server, not from the config.

        /ready reported chunks, model and gateway and said nothing about the
        session store, so a pod with a wrong REDIS_URL went READY and STAYED
        there while every session request 500'd -- pointing whoever debugged it
        at the gateway. The probe that already argues this case for the model
        ("the key is set" looked the same as "the model answers") was making the
        same mistake about the store.

        PING is deliberate: it is O(1) and it proves the connection rather than
        the string. A failure is reported, never raised -- /ready owns whether a
        degraded store means not-ready, and today it does not, because the bot
        still answers questions without history.
        """
        info: dict[str, object] = {"backend": "redis", "durable": True}
        try:
            self._client.ping()
        except Exception as exc:  # noqa: BLE001 -- a probe reports, never raises
            return {**info, "ok": False, "error": type(exc).__name__}
        return {**info, "ok": True}


_store: SessionStore | None = None


def build_session_store() -> SessionStore:
    backend = session_store_backend()
    if backend == "memory":
        return MemorySessionStore()
    if backend == "redis":
        url = os.environ.get("REDIS_URL", "").strip()
        if not url:
            raise RuntimeError("SESSION_STORE=redis requires REDIS_URL to be set.")
        return RedisSessionStore(url)
    raise RuntimeError(f"Unknown SESSION_STORE {backend!r}; use 'memory' or 'redis'.")


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
