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
from collections.abc import Callable
from dataclasses import dataclass

from src.ask import ask, ask_stream

_MENTION_RE = re.compile(r"^\s*<@[^>]+>\s*")

PROMPT_HINT = "Ask an onboarding question, e.g. `how do I enroll a host?`"


def format_response(result: dict) -> str:
    """Render an ask() result as Slack message text with citations."""
    answer = str(result.get("answer", "")).strip()
    citations = [str(c) for c in result.get("citations", []) if c]
    if not answer:
        return PROMPT_HINT
    # Only a salvaged answer is flagged. A blocking-chunked fallback still
    # produced a complete answer, so saying so would be noise to the reader.
    if result.get("degraded") == "salvaged":
        detail = (
            "it may be cut short, and its sources could not be recovered"
            if not citations
            else "it may be cut short"
        )
        answer = (
            f"{answer}\n\n_The connection dropped while this answer was being "
            f"written — {detail}._"
        )
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


@dataclass(frozen=True)
class SlackStreamTarget:
    """How to edit the working Slack message while an answer generates."""

    update_message: Callable[[str], None]
    incremental_updates: bool = True


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
    *,
    raw_question: str,
    update_message: Callable[[str], None],
    incremental_updates: bool = True,
) -> None:
    """Edit a Slack message in place as answer tokens arrive."""
    question = strip_mention(raw_question)
    if not question:
        update_message(PROMPT_HINT)
        return

    accumulated = ""
    last_update = 0.0
    final: dict | None = None

    try:
        for event in ask_stream(question, channel="slack"):
            if event.get("type") == "delta":
                accumulated += event.get("text", "")
                if not incremental_updates:
                    continue
                preview = _format_stream_preview(accumulated)
                now = time.monotonic()
                if now - last_update < STREAM_UPDATE_INTERVAL_SECONDS:
                    continue
                update_message(preview)
                last_update = now
            elif event.get("type") == "done":
                final = event
    except (ValueError, RuntimeError, TimeoutError) as exc:
        update_message(f"Sorry — I hit an error answering that: {exc}")
        return

    if final is None:
        update_message("Sorry — the answer stream ended unexpectedly.")
        return

    update_message(format_response(final))


def _chat_message_updater(
    client,
    *,
    channel: str,
    message_ts: str,
    thread_ts: str | None = None,
) -> Callable[[str], None]:
    """Return an updater that edits a bot message via ``chat.update``."""

    def update_message(text: str) -> None:
        kwargs = {"channel": channel, "ts": message_ts, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        client.chat_update(**kwargs)

    return update_message


def _setup_slash_command_stream(
    client,
    respond,
    *,
    channel: str,
    text: str,
) -> SlackStreamTarget:
    """Post the working note and return how to stream the answer back.

    Prefer ``chat_postMessage`` when the bot is already in the channel so later
    edits can use ``chat.update``. When Slack returns ``not_in_channel``, post
    via Bolt ``respond()`` and replace the working note once at the end.
    Slash-command ``response_url`` posts are limited to five total uses, so
    token-by-token preview updates are not possible on that path.
    """
    from slack_sdk.errors import SlackApiError

    try:
        posted = client.chat_postMessage(channel=channel, text=text)
        return SlackStreamTarget(
            update_message=_chat_message_updater(
                client,
                channel=channel,
                message_ts=str(posted["ts"]),
            ),
            incremental_updates=True,
        )
    except SlackApiError as exc:
        if exc.response.get("error") != "not_in_channel":
            raise

    respond(text=text, response_type="in_channel")

    def update_message(body: str) -> None:
        respond(text=body, replace_original=True)

    return SlackStreamTarget(
        update_message=update_message,
        incremental_updates=False,
    )


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
        text = command.get("text", "")
        stream_target = _setup_slash_command_stream(
            client, respond, channel=channel, text=WORKING_NOTE
        )
        run_in_background(
            lambda: stream_answer_to_slack(
                raw_question=text,
                update_message=stream_target.update_message,
                incremental_updates=stream_target.incremental_updates,
            )
        )

    @app.event("app_mention")
    def handle_mention(event, say):
        thread_ts = event.get("ts")
        channel = event["channel"]
        posted = say(text=WORKING_NOTE, thread_ts=thread_ts)
        message_ts = posted["ts"]
        text = event.get("text", "")
        update_message = _chat_message_updater(
            client,
            channel=channel,
            message_ts=message_ts,
            thread_ts=thread_ts,
        )
        run_in_background(
            lambda: stream_answer_to_slack(
                raw_question=text,
                update_message=update_message,
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
        update_message = _chat_message_updater(
            client,
            channel=channel,
            message_ts=message_ts,
        )
        run_in_background(
            lambda: stream_answer_to_slack(
                raw_question=text,
                update_message=update_message,
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
