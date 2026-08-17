"""Minimal web UI for the onboarding bot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from src.chat import ask_in_session, reset_session
from src.chunker import DATA_DIR, load_chunks
from src.feedback import append_feedback
from src.retrieve import retrieve

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "chat.html"

app = FastAPI(title="Ark Onboarding Bot", docs_url=None, redoc_url=None)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None


class ResetRequest(BaseModel):
    session_id: str


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(max_length=8000)
    sources: list[str] = Field(default_factory=list)
    retrieved_sources: list[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    rating: str = Field(pattern=r"^(up|down)$")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", response_model=None)
def ready() -> dict[str, object] | JSONResponse:
    if not DATA_DIR.is_dir():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "data directory missing"},
        )

    chunks = load_chunks(DATA_DIR)
    if not chunks:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "no corpus chunks"},
        )

    try:
        hits = retrieve("how do I set up Cursor?", k=1)
    except Exception as exc:  # noqa: BLE001 — surface readiness failure to caller
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"retrieval failed: {exc}"},
        )

    if not hits:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "retrieval returned no chunks"},
        )

    return {"status": "ready", "chunks": len(chunks)}


@app.post("/api/ask")
def api_ask(body: AskRequest) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        return ask_in_session(body.session_id, question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/reset")
def api_reset(body: ResetRequest) -> dict[str, bool]:
    reset_session(body.session_id)
    return {"ok": True}


@app.post("/api/feedback")
def api_feedback(body: FeedbackRequest) -> dict[str, bool]:
    append_feedback(
        {
            "question": body.question.strip(),
            "answer": body.answer.strip(),
            "sources": body.sources,
            "retrieved_sources": body.retrieved_sources,
            "session_id": body.session_id,
            "rating": body.rating,
        }
    )
    return {"ok": True}


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    import uvicorn

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8765"))
    print(f"Ark onboarding bot web UI → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
