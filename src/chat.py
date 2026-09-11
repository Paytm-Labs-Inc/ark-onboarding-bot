"""Chat sessions with pluggable persistence (memory or Redis)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any

from src.answer import is_non_answer
from src.ask import ask, ask_stream
from src.citations import parse_citation
from src.session_store import (
    StoredSession,
    StoredTurn,
    compile_history_summary,
    extract_ark_session_id,
    get_session_store,
    history_summary_max_chars,
    max_history_turns,
    stored_to_payload,
    title_from_question,
)

ANONYMOUS_USER = "anonymous"

_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _session_write_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock
        return lock


def _load_session_for_user(session_id: str, user_id: str) -> ChatSession | None:
    stored = get_session_store().load(session_id)
    if stored is None or stored.user_id != user_id:
        return None
    return _session_from_stored(stored)

_DEBUG_META_KEYS = (
    "debug",
    "case",
    "cannot_fix_reason",
    "ark_session_id",
    "gate_id",
    "gate_pending",
    "gate_kind",
    "gate_actions",
    "fix_plan",
)


@dataclass
class ChatTurn:
    question: str
    answer: str
    citations: list[str]
    retrieved_sources: list[str]
    debug: bool = False
    gate_id: str | None = None


@dataclass
class ChatSession:
    session_id: str = ""
    user_id: str = ANONYMOUS_USER
    title: str = ""
    linked_ark_session_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    archived_at: str | None = None
    history_summary: str = ""
    turns: list[ChatTurn] = field(default_factory=list)
    debug_thread: bool = False

    def history_for_prompt(self) -> list[dict[str, str]]:
        cap = max_history_turns()
        if cap <= 0:
            recent = self.turns
        else:
            recent = self.turns[-cap:]
            if len(self.turns) > cap:
                recent = [self.turns[0], *recent]
        history = [{"question": turn.question, "answer": turn.answer} for turn in recent]
        summary = self.history_summary.strip()
        if summary:
            history = [
                {
                    "question": "(Earlier conversation summary)",
                    "answer": summary,
                },
                *history,
            ]
        return history

    def add_turn(
        self,
        question: str,
        answer: str,
        citations: list[str],
        retrieved_sources: list[str],
        *,
        debug: bool = False,
        gate_id: str | None = None,
    ) -> None:
        if not self.title:
            self.title = title_from_question(question)
        linked = extract_ark_session_id(question)
        if linked:
            self.linked_ark_session_id = linked
        self.turns.append(
            ChatTurn(
                question=question,
                answer=answer,
                citations=citations,
                retrieved_sources=retrieved_sources,
                debug=debug,
                gate_id=gate_id,
            )
        )
        self.debug_thread = debug or self.debug_thread


def refresh_history_summary(session: ChatSession) -> None:
    cap = max_history_turns()
    if cap <= 0:
        session.history_summary = ""
        return
    session.history_summary = compile_history_summary(
        session.turns,
        verbatim_cap=cap,
        max_chars=history_summary_max_chars(),
    )


def _turn_to_stored(turn: ChatTurn) -> StoredTurn:
    return StoredTurn(
        question=turn.question,
        answer=turn.answer,
        citations=list(turn.citations),
        retrieved_sources=list(turn.retrieved_sources),
    )


def _stored_to_turn(stored: StoredTurn) -> ChatTurn:
    return ChatTurn(
        question=stored.question,
        answer=stored.answer,
        citations=list(stored.citations),
        retrieved_sources=list(stored.retrieved_sources),
    )


def _session_from_stored(stored: StoredSession) -> ChatSession:
    session = ChatSession(
        session_id=stored.session_id,
        user_id=stored.user_id,
        title=stored.title,
        linked_ark_session_id=stored.linked_ark_session_id,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        archived=stored.archived,
        archived_at=stored.archived_at,
        history_summary=stored.history_summary,
    )
    session.turns = [_stored_to_turn(turn) for turn in stored.turns]
    session.debug_thread = bool(stored.linked_ark_session_id)
    return session


def _session_to_stored(session: ChatSession) -> StoredSession:
    return StoredSession(
        session_id=session.session_id,
        user_id=session.user_id,
        title=session.title,
        linked_ark_session_id=session.linked_ark_session_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        archived=session.archived,
        archived_at=session.archived_at,
        history_summary=session.history_summary,
        turns=[_turn_to_stored(turn) for turn in session.turns],
    )


def get_session(session_id: str | None, user_id: str = ANONYMOUS_USER) -> tuple[str, ChatSession]:
    store = get_session_store()
    if session_id:
        stored = store.load(session_id)
        if stored is not None and stored.user_id == user_id:
            if stored.archived:
                stored.archived = False
                stored.archived_at = None
                store.save(stored)
            return session_id, _session_from_stored(stored)
    new_id = str(uuid.uuid4())
    session = ChatSession(session_id=new_id, user_id=user_id)
    return new_id, session


def save_session(session: ChatSession) -> None:
    refresh_history_summary(session)
    get_session_store().save(_session_to_stored(session))


def reset_session(session_id: str, user_id: str = ANONYMOUS_USER) -> bool:
    store = get_session_store()
    stored = store.load(session_id)
    if stored is None or stored.user_id != user_id:
        return False
    store.delete(session_id)
    return True


def append_debug_artifact_to_session(
    session_id: str,
    *,
    artifact: str,
    question: str | None = None,
    user_id: str = ANONYMOUS_USER,
) -> bool:
    """Append a helper-action artifact to an existing turn, not a new one."""
    chunk = artifact.strip()
    if not chunk:
        return False
    with _session_write_lock(session_id):
        session = _load_session_for_user(session_id, user_id)
        if session is None or not session.turns:
            return False
        idx = len(session.turns) - 1
        if question:
            for i in range(len(session.turns) - 1, -1, -1):
                if session.turns[i].question == question:
                    idx = i
                    break
        turn = session.turns[idx]
        if turn.answer.strip():
            turn.answer = f"{turn.answer}\n\n---\n\n{chunk}"
        else:
            turn.answer = chunk
        save_session(session)
    return True


def _persist_turn(
    sid: str,
    session: ChatSession,
    user_id: str,
    *,
    question: str,
    answer_text: str,
    citations: list[str],
    retrieved_sources: list[str],
    debug: bool = False,
    gate_id: str | None = None,
) -> ChatSession:
    """Add one turn under the session write lock, reloading if already stored."""
    with _session_write_lock(sid):
        reloaded = _load_session_for_user(sid, user_id)
        if reloaded is not None:
            session = reloaded
        session.add_turn(
            question,
            answer_text,
            citations,
            retrieved_sources,
            debug=debug,
            gate_id=gate_id,
        )
        save_session(session)
    return session


def load_session_payload(session_id: str, user_id: str = ANONYMOUS_USER) -> dict[str, Any] | None:
    stored = get_session_store().load(session_id)
    if stored is None or stored.user_id != user_id:
        return None
    return stored_to_payload(stored)


def list_user_sessions(user_id: str, archived: bool = False) -> list[dict[str, Any]]:
    summaries = get_session_store().list_for_user(user_id, archived=archived)
    return [summary.to_dict() for summary in summaries]


def enrich_citations(citations: list[str]) -> list[dict[str, str]]:
    return [parse_citation(source) for source in citations]


def _debug_meta(result: dict[str, Any]) -> dict[str, Any]:
    meta = {key: result[key] for key in _DEBUG_META_KEYS if key in result}
    verdict = result.get("verdict")
    if isinstance(verdict, dict):
        reason = verdict.get("cannot_fix_reason")
        if reason and not meta.get("cannot_fix_reason"):
            meta["cannot_fix_reason"] = reason
    return meta


def ask_in_session(
    session_id: str | None, question: str, user_id: str = ANONYMOUS_USER
) -> dict[str, Any]:
    sid, session = get_session(session_id, user_id=user_id)
    result = ask(
        question,
        history=session.history_for_prompt(),
        channel="web",
        session_id=sid,
        debug_thread=session.debug_thread,
        linked_ark_session_id=session.linked_ark_session_id,
    )
    answer_text = str(result.get("answer", ""))
    citations = [str(item) for item in result.get("citations", [])]
    retrieved_sources = [str(item) for item in result.get("retrieved_sources", [])]
    session = _persist_turn(
        sid,
        session,
        user_id,
        question=question,
        answer_text=answer_text,
        citations=citations,
        retrieved_sources=retrieved_sources,
        debug=bool(result.get("debug")),
        gate_id=result.get("gate_id"),
    )
    payload: dict[str, Any] = {
        "session_id": sid,
        "answer": answer_text,
        "citations": citations,
        "retrieved_sources": retrieved_sources,
        "sources": enrich_citations(citations),
        "handoff": is_non_answer(answer_text) and not result.get("debug"),
    }
    payload.update(_debug_meta(result))
    if session.linked_ark_session_id:
        payload["linked_ark_session_id"] = session.linked_ark_session_id
    return payload


def ask_in_session_stream(
    session_id: str | None, question: str, user_id: str = ANONYMOUS_USER
) -> Iterator[dict[str, Any]]:
    """Like ask_in_session, but yields progress/delta events then a final done payload."""
    sid, session = get_session(session_id, user_id=user_id)

    for event in ask_stream(
        question,
        history=session.history_for_prompt(),
        channel="web",
        session_id=sid,
        debug_thread=session.debug_thread,
        linked_ark_session_id=session.linked_ark_session_id,
    ):
        if event.get("type") == "done":
            answer_text = str(event.get("answer", ""))
            citations = [str(item) for item in event.get("citations", [])]
            retrieved_sources = [
                str(item) for item in event.get("retrieved_sources", [])
            ]
            session = _persist_turn(
                sid,
                session,
                user_id,
                question=question,
                answer_text=answer_text,
                citations=citations,
                retrieved_sources=retrieved_sources,
                debug=bool(event.get("debug")),
                gate_id=event.get("gate_id"),
            )
            final: dict[str, Any] = {
                "type": "done",
                "session_id": sid,
                "answer": answer_text,
                "citations": citations,
                "retrieved_sources": retrieved_sources,
                "sources": enrich_citations(citations),
            }
            if session.linked_ark_session_id:
                final["linked_ark_session_id"] = session.linked_ark_session_id
            if event.get("degraded"):
                final["degraded"] = event["degraded"]
            final["handoff"] = is_non_answer(answer_text) and not event.get("debug")
            final.update(_debug_meta(event))
            yield final
        else:
            yield event
