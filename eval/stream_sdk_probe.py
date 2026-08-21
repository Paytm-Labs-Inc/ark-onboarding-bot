#!/usr/bin/env python3
"""Inspect what cursor-sdk emits while generating (for streaming debug)."""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

PROMPT = 'Respond with JSON only: {"answer":"Streaming probe ok","citations":[]}'


def main() -> int:
    if not os.environ.get("CURSOR_API_KEY", "").strip():
        print("CURSOR_API_KEY is not set.", file=sys.stderr)
        return 1

    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    workspace = os.environ.get("CURSOR_WORKSPACE", os.getcwd())
    model = os.environ.get("CURSOR_MODEL", "claude-haiku-4-5")
    print(f"workspace={workspace}\nmodel={model}\n")

    agent = Agent.create(
        AgentOptions(
            api_key=os.environ["CURSOR_API_KEY"],
            model=model,
            local=LocalAgentOptions(cwd=workspace),
        )
    )

    counts: Counter[str] = Counter()
    text_deltas = 0
    assistant_msgs = 0

    try:
        run = agent.send(PROMPT)
        for event in run.events():
            counts[event.kind or "unknown"] += 1
            update = event.interaction_update
            if update is not None and getattr(update, "type", None) == "text-delta":
                text_deltas += 1
                text = getattr(update, "text", "")
                if text_deltas <= 3:
                    print(f"text-delta: {text[:80]!r}")
            msg = event.sdk_message
            if msg is not None and getattr(msg, "type", "") == "assistant":
                assistant_msgs += 1

        result = run.wait()
        terminal = (getattr(result, "result", None) or "").strip()
        print("\nEvent kinds:", dict(counts))
        print(f"text-delta events: {text_deltas}")
        print(f"assistant sdk_message events: {assistant_msgs}")
        print(f"terminal result length: {len(terminal)}")
        if terminal:
            print(f"terminal preview: {terminal[:120]!r}")
    finally:
        agent.close()

    agent_bin = os.environ.get("CURSOR_AGENT_BIN") or "agent"
    on_path = os.system(f"command -v {agent_bin} >/dev/null") == 0
    print(f"\nagent CLI on PATH: {on_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
