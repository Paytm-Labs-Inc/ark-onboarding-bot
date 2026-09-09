"""Chat sessions with pluggable persistence (memory or Redis)."""

from __future__ import annotations

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


@dataclass
class ChatTurn:
    question: str
    answer: str
    citations: list[str]
    retrieved_sources: list[str]


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

    def history_for_prompt(self) -> list[dict[str, str]]:
        cap = max_history_turns()
        if cap <= 0:
            recent = self.turns
        else:
            recent = self.turns[-cap:]
            if self.turns and self.turns[0] not in recent:
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
            )
        )


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


def ask_in_session(
    session_id: str | None, question: str, user_id: str = ANONYMOUS_USER
) -> dict[str, Any]:
    sid, session = get_session(session_id, user_id=user_id)
    result = ask(
        question,
        history=session.history_for_prompt(),
        channel="web",
        session_id=sid,
    )
    answer_text = str(result.get("answer", ""))
    citations = [str(item) for item in result.get("citations", [])]
    retrieved_sources = [str(item) for item in result.get("retrieved_sources", [])]
    session.add_turn(question, answer_text, citations, retrieved_sources)
    save_session(session)
    payload: dict[str, Any] = {
        "session_id": sid,
        "answer": answer_text,
        "citations": citations,
        "retrieved_sources": retrieved_sources,
        "sources": enrich_citations(citations),
        "handoff": is_non_answer(answer_text),
    }
    if session.linked_ark_session_id:
        payload["linked_ark_session_id"] = session.linked_ark_session_id
    return payload


def ask_in_session_stream(
    session_id: str | None, question: str, user_id: str = ANONYMOUS_USER
) -> Iterator[dict[str, Any]]:
    """Like ask_in_session, but yields delta events then a final done payload."""
    sid, session = get_session(session_id, user_id=user_id)

    for event in ask_stream(
        question,
        history=session.history_for_prompt(),
        channel="web",
        session_id=sid,
    ):
        if event.get("type") == "done":
            answer_text = str(event.get("answer", ""))
            citations = [str(item) for item in event.get("citations", [])]
            retrieved_sources = [
                str(item) for item in event.get("retrieved_sources", [])
            ]
            session.add_turn(question, answer_text, citations, retrieved_sources)
            save_session(session)
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
            final["handoff"] = is_non_answer(answer_text)
            yield final
        else:
            yield event
