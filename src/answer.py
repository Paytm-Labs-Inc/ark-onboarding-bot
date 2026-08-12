"""Grounded answer generation from retrieved doc chunks via Cursor."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

REFUSAL_PHRASE = "I don't have that in the onboarding docs"

DEFAULT_MODEL = "composer-2.5"

SYSTEM_PROMPT = f"""You answer onboarding questions using ONLY the document chunks provided.

Rules:
1. Use only facts explicitly stated in the chunks. Do not use outside knowledge.
2. Do not invent steps, commands, URLs, or policy details.
3. If the chunks do not contain enough information to answer, set answer to exactly:
   "{REFUSAL_PHRASE}"
4. When you answer, cite every chunk source you used in citations.
5. citations must be copied exactly from the chunk source labels provided.
6. Respond with JSON only — no markdown fences, no extra text.

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


def _call_cursor_agent(prompt: str) -> str:
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
    model = os.environ.get("CURSOR_MODEL", DEFAULT_MODEL)

    # Pass the key via env (not argv) so tracebacks never echo it.
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
        model,
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


def answer(question: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a grounded answer and source citations for a question."""
    if not chunks:
        return {"answer": REFUSAL_PHRASE, "citations": []}

    user_content = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Document chunks:\n\n{_format_chunks(chunks)}\n\n"
        f"Question: {question}"
    )

    raw = _call_cursor_agent(user_content)
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
