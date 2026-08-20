"""Grounded answer generation from retrieved doc chunks via Cursor."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

REFUSAL_PHRASE = "I don't have an answer for that yet."

# Answer generation uses the Cursor SDK (preferred) or agent CLI (fallback).
# Override with CURSOR_ANSWER_BACKEND=sdk|cli|auto and CURSOR_MODEL.
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
5. When you answer, list the NUMBER of every chunk you used in chunks_used, e.g. [1, 3].
6. chunks_used must contain only chunk numbers shown above — never source names or URLs.
7. Respond with JSON only — no markdown fences, no extra text. Do not put triple-backtick
   code blocks inside the answer string; use inline backticks for commands (e.g. `ark flow create`).
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
12. For workspace questions: if chunks define a workspace or mention `ark workspace apply`,
    answer with that definition and/or steps (write YAML, apply with `ark workspace apply`,
    list with `ark workspace list`). Do not refuse when those steps appear in the chunks.
13. For workspace ownership or sharing: if chunks mention applying your own workspace YAML,
    dispatching with a workspace name, or team/tenant scoping, explain that workspaces are
    team-scoped, you typically create and apply your own with `ark workspace apply`, and you
    reference a workspace by name at dispatch — do not refuse when those facts appear.
14. For Cursor setup or access: if chunks describe minting a user API key, adding the ark MCP
    server to Cursor (`~/.cursor/mcp.json` or project `.cursor/mcp.json`), or verifying under
    Cursor Settings → MCP, give those steps. Prefer set-up-cursor content over older FAQ lines
    that say Cursor is "in progress" or "not yet" when current setup steps are present.

JSON shape:
{{"answer": "<your answer>", "chunks_used": [1, 3]}}

If refusing, use an empty chunks_used list."""


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        text = chunk.get("text", "")
        parts.append(f"[Chunk {index}]\n{text}")
    return "\n\n".join(parts)


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()

    # Reasoning models (Qwen3 and the R1 family) emit a <think> block first, and it
    # can contain braces, so strip it before looking for the payload. An unterminated
    # block means the reasoning ran past max_tokens and there is no payload after it.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if "<think>" in text.lower():
        text = re.split(r"<think>", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()

    # Haiku wraps the outer payload in ```json; the answer field may contain ``` too.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)

    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    parsed, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object")
    return parsed


def _model_candidates(explicit: str | None) -> list[str]:
    primary = (explicit or os.environ.get("CURSOR_MODEL") or DEFAULT_MODEL).strip()
    if "haiku" in primary.lower():
        ordered = [primary, "claude-haiku-4-5", "haiku-4.5"]
    else:
        fallback = os.environ.get("CURSOR_MODEL_FALLBACK", "composer-2.5-fast").strip()
        ordered = [primary, fallback, "composer-2.5", "auto"]
    seen: set[str] = set()
    out: list[str] = []
    for name in ordered:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _answer_backend() -> str:
    return os.environ.get("CURSOR_ANSWER_BACKEND", "auto").strip().lower()


def _call_cursor_sdk(prompt: str, *, model: str) -> str:
    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as exc:
        raise RuntimeError(
            "cursor-sdk is not installed. Run: pip install cursor-sdk"
        ) from exc

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise ValueError("CURSOR_API_KEY not set")

    workspace = os.environ.get("CURSOR_WORKSPACE", os.getcwd())
    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model,
                local=LocalAgentOptions(cwd=workspace),
            ),
        )
    except Exception as exc:
        raise RuntimeError(f"Cursor SDK request failed: {exc}") from exc
    if getattr(result, "status", None) == "error":
        detail = getattr(result, "result", None) or "unknown SDK error"
        raise RuntimeError(f"Cursor SDK generation failed: {detail}")
    text = getattr(result, "result", None)
    if not text:
        raise RuntimeError("Cursor SDK returned an empty response")
    return str(text).strip()


def _call_cursor_agent_cli(prompt: str, *, model: str) -> str:
    agent_bin = os.environ.get("CURSOR_AGENT_BIN") or shutil.which("agent")
    if not agent_bin:
        raise ValueError(
            "Cursor agent CLI not found on PATH. Install Cursor and ensure `agent` is available."
        )

    workspace = os.environ.get("CURSOR_WORKSPACE", os.getcwd())
    env = os.environ.copy()
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise ValueError("CURSOR_API_KEY not set")
    env["CURSOR_API_KEY"] = api_key
    timeout = int(os.environ.get("CURSOR_TIMEOUT_SECONDS", "180"))

    # Try ask mode first, then plain print mode (Haiku worked via CLI earlier today).
    mode_variants: list[list[str]] = [
        ["--mode", "ask"],
        [],
    ]
    last_detail = "unknown error"
    for mode_args in mode_variants:
        command = [
            agent_bin,
            "-p",
            *mode_args,
            "--output-format",
            "text",
            "--trust",
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
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                "Cursor generation timed out. Try again or increase CURSOR_TIMEOUT_SECONDS."
            ) from exc

        if completed.returncode == 0:
            return completed.stdout.strip()

        last_detail = (completed.stderr or completed.stdout or "unknown error").strip()
        if "Cannot use this model" not in last_detail:
            break

    raise RuntimeError(f"Cursor generation failed: {last_detail}")


def _call_cursor_agent(prompt: str, *, model: str | None = None) -> str:
    """Generate via Cursor SDK (preferred) with agent CLI fallback."""
    backend = _answer_backend()
    models = _model_candidates(model)
    errors: list[str] = []

    for chosen_model in models:
        sdk_tried = False
        if backend in ("auto", "sdk"):
            sdk_tried = True
            try:
                return _call_cursor_sdk(prompt, model=chosen_model)
            except Exception as exc:
                errors.append(f"sdk/{chosen_model}: {exc}")

        if backend in ("auto", "cli") or not sdk_tried:
            try:
                return _call_cursor_agent_cli(prompt, model=chosen_model)
            except (RuntimeError, TimeoutError) as exc:
                errors.append(f"cli/{chosen_model}: {exc}")

        if "Cannot use this model" not in " ".join(errors):
            continue

    detail = errors[-1] if errors else "unknown error"
    raise RuntimeError(f"Cursor generation failed: {detail}")


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
    except Exception:
        # SDK network errors during warmup must not block web/Slack startup.
        pass


def _resolve_citations(
    parsed: dict[str, Any], chunks: list[dict[str, Any]]
) -> list[str]:
    """Map the chunk numbers the model returned back to their source labels.

    Models reliably report which chunk they used but transcribe a 45-character
    source label unreliably, so the prompt asks for numbers and the mapping
    happens here. Responses that still carry verbatim labels (older cache
    entries) are accepted as a fallback.
    """
    sources: list[str] = []

    indices = parsed.get("chunks_used", [])
    if isinstance(indices, list):
        for item in indices:
            try:
                index = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= index <= len(chunks):
                source = chunks[index - 1].get("source")
                if source and str(source) not in sources:
                    sources.append(str(source))
    if sources:
        return sources

    known = {str(chunk["source"]) for chunk in chunks if chunk.get("source")}
    labels = parsed.get("citations", [])
    if isinstance(labels, list):
        for item in labels:
            label = str(item)
            if label in known and label not in sources:
                sources.append(label)
    return sources


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
    citations = _resolve_citations(parsed, chunks)

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
