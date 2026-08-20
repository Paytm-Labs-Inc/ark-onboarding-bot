"""Slack front door for the onboarding bot (Socket Mode).

Slack is just another surface next to the web UI: a slash command (`/ark ...`)
or an @mention forwards the question to the same `ask()` pipeline and posts the
grounded, cited answer back in-thread.

Run as its own process with these env vars:
- SLACK_BOT_TOKEN   (xoxb-...)  — from installing the Slack app
- SLACK_APP_TOKEN   (xapp-...)  — App-Level Token with connections:write (Socket Mode)
- CURSOR_API_KEY               — same key the web app / CLI use
plus the Cursor `agent` CLI on PATH.

Socket Mode means Slack connects to us over an outbound WebSocket, so no public
inbound URL / ingress is needed — it works from an internal host.
"""

from __future__ import annotations

import os
import re
import threading

from src.ask import ask

_MENTION_RE = re.compile(r"^\s*<@[^>]+>\s*")

PROMPT_HINT = "Ask an onboarding question, e.g. `how do I enroll a host?`"


def format_response(result: dict) -> str:
    """Render an ask() result as Slack message text with citations."""
    answer = str(result.get("answer", "")).strip()
    citations = [str(c) for c in result.get("citations", []) if c]
    if not answer:
        return PROMPT_HINT
    if citations:
        cites = "\n".join(f"• {c}" for c in citations)
        return f"{answer}\n\n*Sources*\n{cites}"
    return answer


def strip_mention(text: str) -> str:
    """Drop a leading <@BOTID> mention so only the question remains."""
    return _MENTION_RE.sub("", text or "").strip()


def answer_text(raw_question: str) -> str:
    """Core logic shared by the slash command and mention handlers."""
    question = strip_mention(raw_question)
    if not question:
        return PROMPT_HINT
    return format_response(ask(question))


def should_answer_dm(event: dict) -> bool:
    """True only for human direct messages (ignore channels, bots, edits)."""
    if event.get("channel_type") != "im":
        return False
    if event.get("bot_id") or event.get("subtype"):
        return False
    return True


WORKING_NOTE = "Looking that up in the docs… (this takes ~1-2 min)"


def safe_answer(raw_question: str) -> str:
    """answer_text() but never raises — surface errors as a message instead."""
    try:
        return answer_text(raw_question)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        return f"Sorry — I hit an error answering that: {exc}"


def run_in_background(target) -> None:
    """Run the slow answer off the Slack handler so the socket stays responsive.

    Generating an answer takes ~1-2 min (Cursor agent). Doing it inline blocks
    the Socket Mode connection long enough that Slack drops it ("connection
    reset"), so the reply never posts. Offloading keeps the socket alive and
    lets the reply land when ready.
    """
    threading.Thread(target=target, daemon=True).start()


def build_app():
    """Build the Slack Bolt app. Imported lazily so tests don't need slack_bolt."""
    from slack_bolt import App

    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    @app.command("/askark")
    def handle_command(ack, command, respond):
        ack()
        text = command.get("text", "")
        respond(WORKING_NOTE)
        run_in_background(lambda: respond(safe_answer(text)))

    @app.event("app_mention")
    def handle_mention(event, say):
        thread_ts = event.get("ts")
        text = event.get("text", "")
        say(text=WORKING_NOTE, thread_ts=thread_ts)
        run_in_background(lambda: say(text=safe_answer(text), thread_ts=thread_ts))

    @app.event("message")
    def handle_direct_message(event, say):
        if not should_answer_dm(event):
            return
        text = event.get("text", "")
        say(WORKING_NOTE)
        run_in_background(lambda: say(safe_answer(text)))

    return app


def _maybe_relax_ssl_for_corp_proxy() -> None:
    """Relax Python 3.13+ strict CA checks for corp SSL inspection (Zscaler/Cortex).

    Enable with SLACK_SSL_RELAX=1 when Socket Mode fails with:
    'Basic Constraints of CA cert not marked critical'.
    Use only on managed laptops — not on production hosts with proper CA chains.
    """
    raw = os.environ.get("SLACK_SSL_RELAX", "").strip().lower()
    if raw not in ("1", "true", "yes"):
        return

    import ssl

    if not hasattr(ssl, "VERIFY_X509_STRICT"):
        return

    original = ssl.create_default_context

    def relaxed_create_default_context(*args, **kwargs):
        ctx = original(*args, **kwargs)
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx

    ssl.create_default_context = relaxed_create_default_context  # type: ignore[method-assign]


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    _maybe_relax_ssl_for_corp_proxy()

    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from src.warmup import warm_services

    warm_services()
    app = build_app()
    print("Ark onboarding bot Slack app — connecting via Socket Mode...")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
