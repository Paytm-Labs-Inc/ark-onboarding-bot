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
import time

from src.ask import ask, ask_stream

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
STREAM_UPDATE_INTERVAL_SECONDS = 0.75


def safe_answer(raw_question: str) -> str:
    """answer_text() but never raises — surface errors as a message instead."""
    try:
        return answer_text(raw_question)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        return f"Sorry — I hit an error answering that: {exc}"


def _format_stream_preview(answer_text: str) -> str:
    preview = answer_text.strip()
    if not preview:
        return WORKING_NOTE
    if len(preview) > 3500:
        preview = preview[:3497] + "…"
    return preview


def stream_answer_to_slack(
    client,
    *,
    channel: str,
    message_ts: str,
    raw_question: str,
    thread_ts: str | None = None,
) -> None:
    """Edit a Slack message in place as answer tokens arrive."""
    question = strip_mention(raw_question)
    if not question:
        client.chat_update(channel=channel, ts=message_ts, text=PROMPT_HINT)
        return

    preview = WORKING_NOTE
    accumulated = ""
    last_update = 0.0
    final: dict | None = None

    try:
        for event in ask_stream(question, channel="slack"):
            if event.get("type") == "delta":
                accumulated += event.get("text", "")
                preview = _format_stream_preview(accumulated)
                now = time.monotonic()
                if now - last_update < STREAM_UPDATE_INTERVAL_SECONDS:
                    continue
                client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    text=preview,
                    thread_ts=thread_ts,
                )
                last_update = now
            elif event.get("type") == "done":
                final = event
    except (ValueError, RuntimeError, TimeoutError) as exc:
        client.chat_update(
            channel=channel,
            ts=message_ts,
            text=f"Sorry — I hit an error answering that: {exc}",
            thread_ts=thread_ts,
        )
        return

    if final is None:
        client.chat_update(
            channel=channel,
            ts=message_ts,
            text="Sorry — the answer stream ended unexpectedly.",
            thread_ts=thread_ts,
        )
        return

    client.chat_update(
        channel=channel,
        ts=message_ts,
        text=format_response(final),
        thread_ts=thread_ts,
    )


def _post_slash_working_note(client, respond, *, channel: str, text: str) -> str:
    """Post the in-channel working note for a slash command.

    Prefer ``chat_postMessage`` when the bot is already in the channel so we
    get a stable ``ts`` for ``chat.update``. Fall back to Bolt ``respond()``
    (response_url) when Slack returns ``not_in_channel``.
    """
    from slack_sdk.errors import SlackApiError

    try:
        posted = client.chat_postMessage(channel=channel, text=text)
        return str(posted["ts"])
    except SlackApiError as exc:
        if exc.response.get("error") != "not_in_channel":
            raise
    response = respond(text=text, response_type="in_channel")
    if isinstance(response, dict):
        ts = response.get("ts")
        if ts:
            return str(ts)
        message = response.get("message")
        if isinstance(message, dict) and message.get("ts"):
            return str(message["ts"])
    raise RuntimeError("Slack did not return a message timestamp for the working note")


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
    client = app.client

    @app.command("/askark")
    def handle_command(ack, command, respond):
        ack()
        channel = command["channel_id"]
        message_ts = _post_slash_working_note(
            client, respond, channel=channel, text=WORKING_NOTE
        )
        text = command.get("text", "")
        run_in_background(
            lambda: stream_answer_to_slack(
                client,
                channel=channel,
                message_ts=message_ts,
                raw_question=text,
            )
        )

    @app.event("app_mention")
    def handle_mention(event, say):
        thread_ts = event.get("ts")
        channel = event["channel"]
        posted = say(text=WORKING_NOTE, thread_ts=thread_ts)
        message_ts = posted["ts"]
        text = event.get("text", "")
        run_in_background(
            lambda: stream_answer_to_slack(
                client,
                channel=channel,
                message_ts=message_ts,
                raw_question=text,
                thread_ts=thread_ts,
            )
        )

    @app.event("message")
    def handle_direct_message(event, say):
        if not should_answer_dm(event):
            return
        channel = event["channel"]
        posted = say(WORKING_NOTE)
        message_ts = posted["ts"]
        text = event.get("text", "")
        run_in_background(
            lambda: stream_answer_to_slack(
                client,
                channel=channel,
                message_ts=message_ts,
                raw_question=text,
            )
        )

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
