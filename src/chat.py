"""In-memory chat sessions with short conversation history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any

from src.answer import is_non_answer
from src.ask import ask, ask_stream
from src.citations import parse_citation

MAX_HISTORY_TURNS = 4

_DEBUG_META_KEYS = (
    "debug",
    "case",
    "ark_session_id",
    "gate_id",
    "gate_pending",
    "gate_kind",
    "gate_actions",
    "fix_plan",
    "dispatch_session_id",
    "pr_url",
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
    turns: list[ChatTurn] = field(default_factory=list)
    debug_thread: bool = False

    def history_for_prompt(self) -> list[dict[str, str]]:
        recent = self.turns[-MAX_HISTORY_TURNS:]
        return [{"question": turn.question, "answer": turn.answer} for turn in recent]

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
        self.debug_thread = debug
        if len(self.turns) > MAX_HISTORY_TURNS:
            self.turns = self.turns[-MAX_HISTORY_TURNS:]


_sessions: dict[str, ChatSession] = {}


def get_session(session_id: str | None) -> tuple[str, ChatSession]:
    if session_id and session_id in _sessions:
        return session_id, _sessions[session_id]
    new_id = str(uuid.uuid4())
    session = ChatSession()
    _sessions[new_id] = session
    return new_id, session


def reset_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def enrich_citations(citations: list[str]) -> list[dict[str, str]]:
    return [parse_citation(source) for source in citations]


def _debug_meta(result: dict[str, Any]) -> dict[str, Any]:
    return {key: result[key] for key in _DEBUG_META_KEYS if key in result}


def ask_in_session(session_id: str | None, question: str) -> dict[str, Any]:
    sid, session = get_session(session_id)
    result = ask(
        question,
        history=session.history_for_prompt(),
        channel="web",
        session_id=sid,
        debug_thread=session.debug_thread,
    )
    answer_text = str(result.get("answer", ""))
    citations = [str(item) for item in result.get("citations", [])]
    retrieved_sources = [str(item) for item in result.get("retrieved_sources", [])]
    session.add_turn(
        question,
        answer_text,
        citations,
        retrieved_sources,
        debug=bool(result.get("debug")),
        gate_id=result.get("gate_id"),
    )
    payload = {
        "session_id": sid,
        "answer": answer_text,
        "citations": citations,
        "retrieved_sources": retrieved_sources,
        "sources": enrich_citations(citations),
        "handoff": is_non_answer(answer_text) and not result.get("debug"),
    }
    payload.update(_debug_meta(result))
    return payload


def ask_in_session_stream(
    session_id: str | None, question: str
) -> Iterator[dict[str, Any]]:
    """Like ask_in_session, but yields delta/progress events then a final done payload."""
    sid, session = get_session(session_id)
    final: dict[str, Any] | None = None

    for event in ask_stream(
        question,
        history=session.history_for_prompt(),
        channel="web",
        session_id=sid,
        debug_thread=session.debug_thread,
    ):
        if event.get("type") == "done":
            answer_text = str(event.get("answer", ""))
            citations = [str(item) for item in event.get("citations", [])]
            retrieved_sources = [
                str(item) for item in event.get("retrieved_sources", [])
            ]
            session.add_turn(
                question,
                answer_text,
                citations,
                retrieved_sources,
                debug=bool(event.get("debug")),
                gate_id=event.get("gate_id"),
            )
            final = {
                "type": "done",
                "session_id": sid,
                "answer": answer_text,
                "citations": citations,
                "retrieved_sources": retrieved_sources,
                "sources": enrich_citations(citations),
            }
            if event.get("degraded"):
                final["degraded"] = event["degraded"]
            final["handoff"] = is_non_answer(answer_text) and not event.get("debug")
            final.update(_debug_meta(event))
            yield final
        else:
            yield event
