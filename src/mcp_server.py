"""MCP server exposing the onboarding bot to Cursor, Claude Code and friends.

Runs on the engineer's own machine and calls the *deployed* bot over HTTP, so
there is no new production surface, no second auth path, and nothing to deploy:
it reuses `/api/ask` and the same team token people already paste into the web
UI. The research on internal assistants is consistent that the ones people keep
using are the ones already where they work -- for this audience that is the
editor, not a browser tab.

    ARK_BOT_URL=https://foundry.mypaytm.com/onboarding-bot \
    ARK_ACCESS_TOKEN=... python -m src.mcp_server

The HTTP call and the formatting are deliberately separate from the MCP wiring
below, so both are testable without the `mcp` package installed -- it is in
requirements-mcp.txt rather than requirements.txt, since the production image
has no use for it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "https://foundry.mypaytm.com/onboarding-bot"
DEFAULT_TIMEOUT = 60.0


class AskFailed(RuntimeError):
    """The bot could not be reached, or refused the request."""


def base_url() -> str:
    return os.environ.get("ARK_BOT_URL", DEFAULT_URL).rstrip("/")


def access_token() -> str:
    return os.environ.get("ARK_ACCESS_TOKEN", "").strip()


def ask_remote(
    question: str,
    *,
    url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Any = None,
) -> dict[str, Any]:
    """POST a question to the deployed bot and return its JSON payload.

    `opener` exists so tests can drive this without a network or a live bot.
    """
    question = question.strip()
    if not question:
        raise AskFailed("Ask a question.")

    endpoint = f"{url or base_url()}/api/ask"
    body = json.dumps({"question": question}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, method="POST")
    request.add_header("Content-Type", "application/json")

    bearer = token if token is not None else access_token()
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")

    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 401 is by far the likeliest failure and the least self-explanatory,
        # so it gets told what to do rather than shown a status code.
        if exc.code == 401:
            raise AskFailed(
                "The bot rejected the token. Set ARK_ACCESS_TOKEN to the same "
                "value you use to sign in to the web UI."
            ) from exc
        raise AskFailed(f"The bot returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise AskFailed(
            f"Could not reach the bot at {endpoint}. Check ARK_BOT_URL, and that "
            "you are on the corporate network."
        ) from exc


def format_answer(payload: dict[str, Any]) -> str:
    """Render the answer with its sources, which are the point of citing at all."""
    answer = str(payload.get("answer", "")).strip() or "No answer returned."
    citations = [str(item) for item in payload.get("citations") or []]
    if not citations:
        return answer
    sources = "\n".join(f"- {item}" for item in citations)
    return f"{answer}\n\nSources:\n{sources}"


def main() -> None:
    """Serve over stdio. Imported lazily so the module stays testable without mcp."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:  # pragma: no cover -- exercised by running without the dep
        raise SystemExit(
            "The MCP server needs the `mcp` package: "
            "pip install -r requirements-mcp.txt"
        )

    server = FastMCP("ark-onboarding-bot")

    @server.tool()
    def ask_ark_onboarding(question: str) -> str:
        """Answer a question about onboarding onto the Ark platform.

        Grounded in the Foundry onboarding documentation, with the source page
        cited. Says so plainly when the docs do not cover the question rather
        than guessing, so a refusal is information rather than a failure.

        Good for: getting access, enrolling compute, secrets and credentials,
        running a first session, setting up Cursor, and what to check when
        something breaks.
        """
        return format_answer(ask_remote(question))

    server.run()


if __name__ == "__main__":
    main()
