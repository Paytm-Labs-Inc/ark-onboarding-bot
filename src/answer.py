"""Grounded answer generation from retrieved doc chunks via Cursor."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

REFUSAL_PHRASE = "I don't have an answer for that yet."

DEFAULT_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = f"""You answer Ark setup and usage questions using ONLY the document chunks provided.

Rules:
1. Use only facts explicitly stated in the chunks. Do not use outside knowledge.
2. Do not invent steps, commands, URLs, or policy details.
3. Document chunks are provided — you MUST synthesize an answer from them when any chunk
   mentions the topic, even if the answer is partial or spread across chunks. Do not refuse
   merely because no single chunk is a perfect match.
4. Refuse (set answer to exactly "{REFUSAL_PHRASE}") ONLY when:
   (a) the question is genuinely out of scope for Ark onboarding — e.g. resetting a Jira or
       Bitbucket password, deploying an app to AWS production, internal prod connection
       strings, bypassing Ark auth, or generic CI setup unrelated to Ark; OR
   (b) the chunks contain zero facts relevant to the question (nothing on-topic to synthesize).
5. When you answer, cite every chunk source you used in citations.
6. citations must be copied exactly from the chunk source labels provided.
7. Respond with JSON only — no markdown fences, no extra text.
8. Write directly to the user in second person ("You can…", "Use…", "Run…"). Never refer to
   "onboarding docs", "the docs", "documentation", "these docs", "the chunks", "provided
   sources", or meta phrases like "the docs do not mention" or "is not covered in the docs".
   State what to do or what is supported instead (e.g. "Use Claude Code or Cursor via MCP"
   not "the docs recommend Claude and Cursor").
9. For onboarding steps or order questions: if chunks include an "Onboarding path"
   numbered list and/or a "correct onboarding order" checklist, present the FULL
   ordered list with concrete steps. Do not answer with only MCP setup when a broader
   checklist is present in the chunks.
10. For "how to use Ark" / post-onboarding questions: give concrete operational steps
    (register agents, `ark workspace apply`, `ark flow create`, dispatch a session,
    then watch progress). Do not lead with "Ark is not a chatbot" unless the user
    explicitly asks what Ark is.
11. For tool/client choice (Claude vs Cursor vs OpenAI): say which clients Ark supports
    and how to connect each. Do not discuss what documentation omits — say what works.

JSON shape:
{{"answer": "<your answer>", "citations": ["<source label>", "..."]}}

If refusing, use an empty citations list."""


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        source = chunk.get("source", f"chunk-{index}")
        text = chunk.get("text", "")
        parts.append(f"[Chunk {index} — {source}]\n{text}")
    return "\n\n".join(parts)


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    return json.loads(text)


def _call_cursor_agent(prompt: str, *, model: str | None = None) -> str:
    """Run a one-shot ask-mode generation through the Cursor agent CLI."""
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise ValueError("CURSOR_API_KEY not set")

    agent_bin = os.environ.get("CURSOR_AGENT_BIN") or shutil.which("agent")
    if not agent_bin:
        raise ValueError(
            "Cursor agent CLI not found on PATH. Install Cursor and ensure `agent` is available."
        )

    workspace = os.environ.get("CURSOR_WORKSPACE", os.getcwd())
    chosen_model = model or os.environ.get("CURSOR_MODEL", DEFAULT_MODEL)

    env = os.environ.copy()
    env["CURSOR_API_KEY"] = api_key

    command = [
        agent_bin,
        "-p",
        "--mode",
        "ask",
        "--output-format",
        "text",
        "--trust",
        "--approve-mcps",
        "--model",
        chosen_model,
        "--workspace",
        workspace,
        prompt,
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("CURSOR_TIMEOUT_SECONDS", "180")),
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "Cursor generation timed out. Try again or increase CURSOR_TIMEOUT_SECONDS."
        ) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise RuntimeError(f"Cursor generation failed: {detail}")

    return completed.stdout.strip()


def warm_agent() -> None:
    """Ping the Cursor agent CLI so the first user ask is not cold."""
    if not os.environ.get("CURSOR_API_KEY", "").strip():
        return
    raw = os.environ.get("WARM_AGENT_ON_STARTUP", "1").strip().lower()
    if raw in ("0", "false", "no"):
        return
    try:
        _call_cursor_agent('Respond with JSON only: {"answer":"ok","citations":[]}')
    except (ValueError, RuntimeError, TimeoutError):
        pass


def _format_history(history: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for turn in history:
        question = turn.get("question", "").strip()
        answer_text = turn.get("answer", "").strip()
        if not question:
            continue
        lines.append(f"User: {question}")
        if answer_text:
            lines.append(f"Assistant: {answer_text}")
    return "\n".join(lines)


def _generate_answer(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    history_block = ""
    if history:
        formatted = _format_history(history)
        if formatted:
            history_block = (
                "Recent conversation (follow-up context only — "
                "still answer ONLY from the document chunks):\n"
                f"{formatted}\n\n"
            )

    user_content = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Document chunks:\n\n{_format_chunks(chunks)}\n\n"
        f"{history_block}"
        f"Question: {question}"
    )

    raw = _call_cursor_agent(user_content, model=model)
    try:
        parsed = _parse_json_response(raw)
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        raise ValueError(f"Could not parse model response as JSON: {raw!r}") from exc

    answer_text = str(parsed.get("answer", "")).strip()
    citations_raw = parsed.get("citations", [])
    if not isinstance(citations_raw, list):
        citations_raw = []

    known_sources = {chunk.get("source") for chunk in chunks if chunk.get("source")}
    citations = [str(item) for item in citations_raw if str(item) in known_sources]

    if answer_text != REFUSAL_PHRASE and not citations:
        citations = [
            str(chunk["source"])
            for chunk in chunks
            if chunk.get("source") and str(chunk["source"]).lower() in answer_text.lower()
        ]

    return {"answer": answer_text or REFUSAL_PHRASE, "citations": citations}


def answer(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return a grounded answer and source citations for a question."""
    if not chunks:
        return {"answer": REFUSAL_PHRASE, "citations": []}

    return _generate_answer(question, chunks, history=history)
