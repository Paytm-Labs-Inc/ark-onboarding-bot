"""In-memory chat sessions with short conversation history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from src.ask import ask
from src.citations import parse_citation

MAX_HISTORY_TURNS = 4


@dataclass
class ChatTurn:
    question: str
    answer: str
    citations: list[str]
    retrieved_sources: list[str]


@dataclass
class ChatSession:
    turns: list[ChatTurn] = field(default_factory=list)

    def history_for_prompt(self) -> list[dict[str, str]]:
        recent = self.turns[-MAX_HISTORY_TURNS:]
        return [{"question": turn.question, "answer": turn.answer} for turn in recent]

    def add_turn(
        self,
        question: str,
        answer: str,
        citations: list[str],
        retrieved_sources: list[str],
    ) -> None:
        self.turns.append(
            ChatTurn(
                question=question,
                answer=answer,
                citations=citations,
                retrieved_sources=retrieved_sources,
            )
        )
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


def ask_in_session(session_id: str | None, question: str) -> dict[str, Any]:
    sid, session = get_session(session_id)
    result = ask(question, history=session.history_for_prompt())
    answer_text = str(result.get("answer", ""))
    citations = [str(item) for item in result.get("citations", [])]
    retrieved_sources = [str(item) for item in result.get("retrieved_sources", [])]
    session.add_turn(question, answer_text, citations, retrieved_sources)
    return {
        "session_id": sid,
        "answer": answer_text,
        "citations": citations,
        "retrieved_sources": retrieved_sources,
        "sources": enrich_citations(citations),
    }
