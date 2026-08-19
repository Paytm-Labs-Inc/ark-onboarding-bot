"""Minimal web UI for the onboarding bot."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.auth import (
    COOKIE_NAME,
    PUBLIC_PATHS,
    auth_enabled,
    request_authorized,
    token_valid,
)
from src.chat import ask_in_session, reset_session
from src.chunker import DATA_DIR, load_chunks
from src.feedback import append_feedback, read_feedback
from src.retrieve import retrieve

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_PATH = TEMPLATE_DIR / "chat.html"
LOGIN_TEMPLATE_PATH = TEMPLATE_DIR / "login.html"

# 12 hours; a shared team token doesn't need long-lived sessions.
SESSION_MAX_AGE = 12 * 60 * 60

app = FastAPI(title="Ark Onboarding Bot", docs_url=None, redoc_url=None)


@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    """Gate every route except infra probes and the login flow behind the token."""
    if request.url.path in PUBLIC_PATHS or request_authorized(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required. Sign in at /login."},
        )
    return RedirectResponse(url="/login", status_code=303)


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


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return LOGIN_TEMPLATE_PATH.read_text(encoding="utf-8")


@app.post("/login")
def login_submit(body: LoginRequest) -> Response:
    if auth_enabled() and not token_valid(body.token.strip()):
        return JSONResponse(status_code=401, content={"detail": "Invalid token."})
    response = JSONResponse(content={"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        body.token.strip(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/reviews", response_class=HTMLResponse)
def reviews_page() -> str:
    return render_reviews(read_feedback())


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


def render_reviews(records: list[dict]) -> str:
    """Server-render the feedback review page (no template engine needed)."""
    ups = sum(1 for r in records if r.get("rating") == "up")
    downs = sum(1 for r in records if r.get("rating") == "down")

    rows: list[str] = []
    for record in records:
        rating = record.get("rating")
        badge = "up" if rating == "up" else "down"
        icon = "👍" if rating == "up" else "👎"
        sources = record.get("sources") or record.get("retrieved_sources") or []
        source_labels = ", ".join(html.escape(str(s)) for s in sources) or "—"
        rows.append(
            f"""
        <tr class="{badge}">
          <td class="rating">{icon}</td>
          <td>
            <div class="q">{html.escape(str(record.get("question", "")))}</div>
            <div class="a">{html.escape(str(record.get("answer", "")))}</div>
            <div class="src">{source_labels}</div>
          </td>
          <td class="ts">{html.escape(str(record.get("ts", "")))}</td>
        </tr>"""
        )

    body = "".join(rows) or (
        '<tr><td colspan="3" class="empty">No feedback yet. '
        "Thumbs up/down in the chat will show up here.</td></tr>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ark Onboarding Bot — Feedback</title>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background:#f6f7f9; color:#1f2937; }}
    .wrap {{ max-width:900px; margin:0 auto; padding:24px 16px 40px; }}
    header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }}
    h1 {{ font-size:1.3rem; margin:0; }}
    a.back {{ color:#2563eb; text-decoration:none; font-size:0.9rem; }}
    .counts {{ color:#6b7280; margin-bottom:16px; font-size:0.92rem; }}
    table {{ width:100%; border-collapse:collapse; background:#fff;
      border:1px solid #e5e7eb; border-radius:12px; overflow:hidden; }}
    th, td {{ text-align:left; padding:12px 14px; border-bottom:1px solid #eef0f2; vertical-align:top; }}
    th {{ font-size:0.78rem; text-transform:uppercase; letter-spacing:0.04em; color:#6b7280; }}
    td.rating {{ font-size:1.2rem; width:44px; }}
    tr.down td.rating {{ color:#b91c1c; }}
    .q {{ font-weight:600; }}
    .a {{ color:#374151; margin-top:4px; white-space:pre-wrap; }}
    .src {{ color:#6b7280; font-size:0.82rem; margin-top:6px; }}
    td.ts {{ color:#9ca3af; font-size:0.8rem; white-space:nowrap; }}
    td.empty {{ text-align:center; color:#6b7280; padding:28px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Feedback review</h1>
      <a class="back" href="/">&larr; Back to chat</a>
    </header>
    <div class="counts">{ups} 👍 &nbsp; {downs} 👎 &nbsp; ({len(records)} total)</div>
    <table>
      <thead><tr><th>Rating</th><th>Question / answer</th><th>When</th></tr></thead>
      <tbody>{body}
      </tbody>
    </table>
  </div>
</body>
</html>"""


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    import uvicorn

    from src.auth import LOOPBACK_HOSTS

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8765"))

    if host not in LOOPBACK_HOSTS and not auth_enabled():
        raise SystemExit(
            f"Refusing to bind to non-loopback host {host!r} without ARK_ACCESS_TOKEN "
            "set — that would expose the UI with no access control. Set "
            "ARK_ACCESS_TOKEN (recommended) or use WEB_HOST=127.0.0.1 behind an SSH tunnel."
        )
    if not auth_enabled():
        print(
            "WARNING: ARK_ACCESS_TOKEN is not set — the UI is OPEN to anyone who can "
            "reach it. Set ARK_ACCESS_TOKEN to gate access to the team.",
        )

    print(f"Ark onboarding bot web UI → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
