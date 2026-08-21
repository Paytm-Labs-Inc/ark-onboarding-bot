#!/usr/bin/env python3
"""Inspect cursor-sdk / agent CLI streaming capabilities."""

from __future__ import annotations

import os
import shutil
import subprocess
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


def _agent_on_path() -> str | None:
    return os.environ.get("CURSOR_AGENT_BIN") or shutil.which("agent")


def _probe_cli(agent_bin: str) -> int:
    print("--- agent CLI ---")
    try:
        completed = subprocess.run(
            [agent_bin, "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"agent status failed: {exc}")
        return 1

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        print(f"agent status exit {completed.returncode}: {detail[:300]}")
        return 1

    print(completed.stdout.strip() or "(agent status ok, no stdout)")
    print("\nCLI looks usable. For corp networks, set in .env:")
    print("  CURSOR_ANSWER_BACKEND=cli")
    return 0


def _probe_sdk() -> int:
    print("--- cursor-sdk ---")
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    workspace = os.environ.get("CURSOR_WORKSPACE", os.getcwd())
    model = os.environ.get("CURSOR_MODEL", "claude-haiku-4-5")
    print(f"workspace={workspace}")
    print(f"model={model}\n")

    try:
        agent = Agent.create(
            AgentOptions(
                api_key=os.environ["CURSOR_API_KEY"],
                model=model,
                local=LocalAgentOptions(cwd=workspace),
            )
        )
    except Exception as exc:
        print(f"SDK CreateAgent failed: {exc}")
        print(
            "\nThis usually means the SDK cannot reach Cursor (corp proxy/VPN/Zscaler).\n"
            "Use the agent CLI instead:\n"
            "  1. Ensure `agent status` works\n"
            "  2. Add to .env: CURSOR_ANSWER_BACKEND=cli\n"
            "Blocking answers already fall back to CLI in auto mode; streaming tries CLI next.",
            file=sys.stderr,
        )
        return 1

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

    return 0


def main() -> int:
    if not os.environ.get("CURSOR_API_KEY", "").strip():
        print("CURSOR_API_KEY is not set.", file=sys.stderr)
        return 1

    agent_bin = _agent_on_path()
    print(f"agent CLI on PATH: {bool(agent_bin)}")
    if agent_bin:
        print(f"agent binary: {agent_bin}\n")
        cli_rc = _probe_cli(agent_bin)
    else:
        print("Install Cursor CLI: curl https://cursor.com/install -fsS | bash\n")
        cli_rc = 1

    sdk_rc = _probe_sdk()
    return 0 if cli_rc == 0 or sdk_rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
