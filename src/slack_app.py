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


def build_app():
    """Build the Slack Bolt app. Imported lazily so tests don't need slack_bolt."""
    from slack_bolt import App

    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    @app.command("/ark")
    def handle_command(ack, command, respond):
        ack()
        respond(answer_text(command.get("text", "")))

    @app.event("app_mention")
    def handle_mention(event, say):
        say(text=answer_text(event.get("text", "")), thread_ts=event.get("ts"))

    @app.event("message")
    def handle_direct_message(event, say):
        if should_answer_dm(event):
            say(answer_text(event.get("text", "")))

    return app


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app()
    print("Ark onboarding bot Slack app — connecting via Socket Mode...")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
