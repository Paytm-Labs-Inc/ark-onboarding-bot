"""Minimal web UI for the onboarding bot."""

from __future__ import annotations

import html
import json
import anyio
import os
import sys
from collections import defaultdict, deque
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from src.answer import PiAtCapacity, missing_backend_credential
from src.auth import (
    COOKIE_NAME,
    PUBLIC_PATHS,
    auth_enabled,
    request_authorized,
    token_valid,
)
from src.chat import ask_in_session, ask_in_session_stream, reset_session
from src.feedback import append_feedback, read_feedback
from src.warmup import check_retrieval_ready, warm_services

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_PATH = TEMPLATE_DIR / "chat.html"
LOGIN_TEMPLATE_PATH = TEMPLATE_DIR / "login.html"

# 12 hours; a shared team token doesn't need long-lived sessions.
SESSION_MAX_AGE = 12 * 60 * 60


def base_path() -> str:
    """URL prefix when served under a subpath (e.g. "/onboarding-bot").

    Read from BASE_PATH (falls back to ROOT_PATH) at call time so the prefix can
    be set via env with no code change. Empty means served at the domain root.
    Outgoing links/redirects are always prefixed with this; incoming requests
    are normalized by PrefixStripMiddleware, so the app works whether the ingress
    strips the prefix or passes it through.
    """
    prefix = os.environ.get("BASE_PATH") or os.environ.get("ROOT_PATH", "")
    return prefix.rstrip("/")


def base_href() -> str:
    """`<base>` value so in-page relative URLs resolve under the prefix."""
    prefix = base_path()
    return f"{prefix}/" if prefix else "/"


def _render_template(path: Path) -> str:
    """Load a template and inject the <base> tag for the current prefix."""
    return path.read_text(encoding="utf-8").replace(
        "<!--BASE_TAG-->", f'<base href="{base_href()}">'
    )


class PrefixStripMiddleware:
    """Strip BASE_PATH from incoming request paths when it is present.

    Lets one app work regardless of ingress behavior:
    - ingress strips the prefix -> path already lacks it -> left untouched.
    - ingress passes the prefix through -> path starts with it -> stripped here
      so routing (defined at "/login", "/api/ask", ...) still matches.
    Outgoing URLs are prefixed separately via base_path(), so links/redirects
    stay correct in both modes.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            prefix = base_path()
            path = scope.get("path", "")
            if prefix and (path == prefix or path.startswith(prefix + "/")):
                scope = dict(scope)
                new_path = path[len(prefix):] or "/"
                scope["path"] = new_path
                if scope.get("raw_path") is not None:
                    query = scope.get("query_string", b"")
                    raw = new_path.encode("utf-8")
                    scope["raw_path"] = raw + (b"?" + query if query else b"")
        await self.app(scope, receive, send)


def threadpool_size() -> int:
    """Worker threads for sync routes; the answer path holds one per question."""
    raw = os.environ.get("WEB_THREADPOOL_SIZE", "64").strip()
    try:
        return max(8, int(raw)) if raw else 64
    except ValueError:
        return 64


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Every sync route shares anyio's default thread pool (40 tokens out of the
    # box). A Pi stall pins a thread for up to ~60 s, so forty slow answers
    # used to queue the probes behind them and k8s restarted a busy pod.
    anyio.to_thread.current_default_thread_limiter().total_tokens = threadpool_size()
    warm_services()
    yield


app = FastAPI(
    title="Ark Onboarding Bot",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


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
    return RedirectResponse(url=f"{base_path()}/login", status_code=303)


# Middleware added later wraps what came before. security_headers (below) is
# added after enforce_auth, so it stamps every response including the 401 and
# the login redirect; PrefixStrip is added last and is outermost, so the path
# is normalised before anything else sees it.
app.add_middleware(PrefixStripMiddleware)


# Per-client sliding window on the answer endpoints. The bot is an internal
# tool, but it fronts a model anyone reachable can drive, and one loop can burn
# the gateway's concurrency for everyone. In-process on purpose: it protects
# one pod, which is the deployment shape for the beta.
_ASK_WINDOW_SECONDS = 60.0
# Keys are client addresses. With a wildcard trusted-proxy list any caller
# could mint a fresh key per request via X-Forwarded-For, so main() no longer
# trusts every peer (see FORWARDED_ALLOW_IPS) -- and the table is bounded
# regardless, so a flood of keys cannot become a memory-growth vector.
_ASK_MAX_TRACKED_CLIENTS = 5000
_ask_hits: dict[str, deque[float]] = defaultdict(deque)
_ask_hits_lock = threading.Lock()


def ask_rate_limit_per_minute() -> int:
    """Answers allowed per client per minute; 0 disables the limit.

    A set-but-empty variable (the .env.example house style) must not turn
    into a 500 on every question, so parse leniently and say what happened.
    """
    raw = os.environ.get("ASK_RATE_LIMIT_PER_MINUTE", "30").strip()
    try:
        return int(raw) if raw else 30
    except ValueError:
        print(f"ASK_RATE_LIMIT_PER_MINUTE={raw!r} is not a number; using 30", flush=True)
        return 30


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _now() -> float:
    # Indirection so tests can drive the limiter's clock without patching the
    # time module the ASGI test client itself depends on.
    return time.monotonic()


def enforce_ask_rate_limit(request: Request) -> None:
    limit = ask_rate_limit_per_minute()
    if limit <= 0:
        return
    now = _now()
    key = _client_key(request)
    with _ask_hits_lock:
        if len(_ask_hits) >= _ASK_MAX_TRACKED_CLIENTS:
            _evict_idle_clients(now)
        hits = _ask_hits[key]
        while hits and now - hits[0] > _ASK_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Too many questions; limit is {limit} per minute. Try again shortly.",
            )
        hits.append(now)


def _evict_idle_clients(now: float) -> None:
    """Drop clients with no hit inside the window; called under the lock."""
    idle = [k for k, hits in _ask_hits.items() if not hits or now - hits[-1] > _ASK_WINDOW_SECONDS]
    for k in idle:
        del _ask_hits[k]
    if len(_ask_hits) >= _ASK_MAX_TRACKED_CLIENTS:
        # Still full of active clients: shed the least-recently-seen half
        # rather than grow. Those clients get a fresh window -- a deliberate
        # loosening under pressure, chosen over unbounded memory on one pod.
        for k in sorted(_ask_hits, key=lambda k: _ask_hits[k][-1])[: len(_ask_hits) // 2]:
            del _ask_hits[k]


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
    return _render_template(TEMPLATE_PATH)


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return _render_template(LOGIN_TEMPLATE_PATH)


@app.post("/login")
def login_submit(request: Request, body: LoginRequest) -> Response:
    if auth_enabled() and not token_valid(body.token.strip()):
        return JSONResponse(status_code=401, content={"detail": "Invalid token."})
    response = JSONResponse(content={"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        body.token.strip(),
        max_age=SESSION_MAX_AGE,
        path=base_path() or "/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.get("/logout")
def logout() -> Response:
    response = RedirectResponse(url=f"{base_path()}/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path=base_path() or "/")
    return response


@app.get("/reviews", response_class=HTMLResponse)
def reviews_page() -> str:
    return render_reviews(read_feedback())


# Upstream failures name hostnames, model ids and gateway messages. Those go
# to the log; the user gets a sentence they can act on.
TIMEOUT_MESSAGE = "The answer service took too long. Please try again."
BAD_REQUEST_MESSAGE = "That question could not be processed. Please rephrase and try again."
UNAVAILABLE_MESSAGE = "The answer service is unavailable right now. Please try again shortly."
BUSY_MESSAGE = "The answer service is busy. Please try again in a few seconds."


def _log_upstream_failure(exc: BaseException) -> None:
    print(f"answer failed: {type(exc).__name__}: {exc}", flush=True)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    # The templates are fully inline (no external scripts, styles or fonts),
    # so the policy can be strict about origins while allowing inline code.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
    )
    return response


# Probes run on their own two-thread limiter, never behind an answer.
_PROBE_LIMITER = anyio.CapacityLimiter(2)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", response_model=None)
async def ready() -> dict[str, object] | JSONResponse:
    ok, body = await anyio.to_thread.run_sync(check_retrieval_ready, limiter=_PROBE_LIMITER)
    if not ok:
        return JSONResponse(status_code=503, content=body)
    missing = missing_backend_credential()
    if missing:
        return JSONResponse(
            status_code=503, content={**body, "status": "not_ready", "reason": missing}
        )
    return body


@app.post("/api/ask")
def api_ask(body: AskRequest, _limit: None = Depends(enforce_ask_rate_limit)) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        return ask_in_session(body.session_id, question)
    except ValueError as exc:
        # Config errors surface as ValueError too (a missing key, bad
        # PI_EXTRA_PARAMS) and their text names the config; log it, say
        # something the user can act on.
        _log_upstream_failure(exc)
        raise HTTPException(status_code=400, detail=BAD_REQUEST_MESSAGE) from exc
    except TimeoutError as exc:
        _log_upstream_failure(exc)
        raise HTTPException(status_code=504, detail=TIMEOUT_MESSAGE) from exc
    except PiAtCapacity as exc:
        # Distinct from other RuntimeErrors: this one is temporary by definition.
        raise HTTPException(status_code=503, detail=BUSY_MESSAGE, headers={"Retry-After": "10"}) from exc
    except RuntimeError as exc:
        _log_upstream_failure(exc)
        raise HTTPException(status_code=502, detail=UNAVAILABLE_MESSAGE) from exc


def _sse_event(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _ask_stream_events(session_id: str | None, question: str) -> Iterator[str]:
    try:
        for event in ask_in_session_stream(session_id, question):
            yield _sse_event(event)
    except ValueError as exc:
        _log_upstream_failure(exc)
        yield _sse_event({"type": "error", "detail": BAD_REQUEST_MESSAGE})
    except TimeoutError as exc:
        _log_upstream_failure(exc)
        yield _sse_event({"type": "error", "detail": TIMEOUT_MESSAGE})
    except PiAtCapacity:
        yield _sse_event({"type": "error", "detail": BUSY_MESSAGE})
    except RuntimeError as exc:
        _log_upstream_failure(exc)
        yield _sse_event({"type": "error", "detail": UNAVAILABLE_MESSAGE})


@app.post("/api/ask/stream")
def api_ask_stream(
    body: AskRequest, _limit: None = Depends(enforce_ask_rate_limit)
) -> StreamingResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    return StreamingResponse(
        _ask_stream_events(body.session_id, question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/reset")
def api_reset(body: ResetRequest) -> dict[str, bool]:
    reset_session(body.session_id)
    return {"ok": True}


@app.post("/api/feedback")
def api_feedback(body: FeedbackRequest) -> dict[str, bool]:
    try:
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
    except OSError as exc:
        # Here the write is the request, so say so honestly rather than 500 --
        # and say it in the log too, or availability improves while visibility drops.
        print(f"feedback write failed: {exc}", flush=True)
        raise HTTPException(status_code=503, detail="Feedback could not be saved right now.") from exc
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
  <base href="{base_href()}">
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
      <a class="back" href=".">&larr; Back to chat</a>
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

    missing = missing_backend_credential()
    if missing:
        raise SystemExit(f"Refusing to start: {missing}")
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

    prefix = base_path()
    if prefix:
        print(f"Serving under subpath prefix {prefix!r} (expects a reverse proxy to strip it).")
    print(f"Ark onboarding bot web UI → http://{host}:{port}{prefix}/")
    uvicorn.run(app, host=host, port=port, **proxy_settings())


def proxy_settings() -> dict[str, object]:
    """How uvicorn treats X-Forwarded-For / -Proto, from FORWARDED_ALLOW_IPS.

    Unset: no proxy headers are honoured at all, so the rate-limit key is the
    real peer and a client cannot mint keys with a header (review measured
    that trusting loopback still let SSH-tunnel users do exactly that).
    Set to the ingress CIDR: headers from the ingress are honoured, which is
    what gives per-user keys and a Secure login cookie behind TLS termination.
    "*" trusts every caller. It is the platform's interim while the pod sits
    behind a ClusterIP-only Service (only the ingress can reach it), so it is
    accepted with a warning rather than refused; anywhere a client can reach
    the pod directly it lets that client mint its own rate-limit key.
    """
    trusted = os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    if trusted == "*":
        print(
            "WARNING: FORWARDED_ALLOW_IPS='*' trusts every peer's X-Forwarded-* "
            "headers. Acceptable only while nothing but the ingress can reach this "
            "process; replace with the ingress address or CIDR when known.",
            file=sys.stderr,
        )
    if not trusted:
        return {"proxy_headers": False}
    return {"proxy_headers": True, "forwarded_allow_ips": trusted}


if __name__ == "__main__":
    main()
