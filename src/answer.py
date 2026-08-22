"""Grounded answer generation from retrieved doc chunks via Cursor."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import httpx

# Two ways to decline. Out-of-scope questions get REFUSAL_PHRASE; on-topic Ark
# questions we simply have no material for get ROADMAP_PHRASE, so a missing
# feature reads as "not yet" rather than "no".
REFUSAL_PHRASE = "I don't have an answer for that yet."
ROADMAP_PHRASE = "We have this on our roadmap and are working towards it."


def is_non_answer(text: str) -> bool:
    """True when the answer is a decline rather than a grounded answer."""
    return text.strip() in (REFUSAL_PHRASE, ROADMAP_PHRASE)

# Which provider generates the answer. `pi` posts to the Pi Inference gateway,
# an OpenAI-compatible completions endpoint. `cursor` drives the Cursor agent,
# which boots a workspace session per question and is roughly 10x slower.
# Override with ANSWER_BACKEND=pi|cursor.
DEFAULT_BACKEND = "pi"

# Pi Inference. Note the host: app.inference.paytm.com is the control plane and
# 404s on completions; inference lives on api.inference.paytm.com.
PI_DEFAULT_BASE_URL = "https://api.inference.paytm.com"
PI_DEFAULT_MODEL = "qwen/qwen3-32b"

# Request parameters certain models need. Qwen3 is a hybrid reasoning model:
# without these it spends the token budget on a <think> block and never emits
# the JSON payload. Setting them also cuts output tokens ~30x.
PI_MODEL_PARAMS: dict[str, dict[str, Any]] = {
    "qwen/qwen3-32b": {
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
    },
}

# Cursor backend. Override with CURSOR_ANSWER_BACKEND=sdk|cli|auto and CURSOR_MODEL.
DEFAULT_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = f"""You answer Ark setup and usage questions using ONLY the document chunks provided.

Rules:
1. Use only facts explicitly stated in the chunks. Do not use outside knowledge.
2. Do not invent steps, commands, URLs, or policy details.
3. Document chunks are provided — you MUST synthesize an answer from them when any chunk
   mentions the topic, even if the answer is partial or spread across chunks. Do not refuse
   merely because no single chunk is a perfect match.
4. Decline ONLY in these two cases, and use the exact wording given:
   (a) The question is not about Ark at all — resetting a Jira or Bitbucket password,
       general programming help, company or financial information, deploying an app to AWS
       production, generic CI setup — or it asks you to subvert Ark, such as bypassing auth
       or revealing internal connection strings.
       Answer exactly: "{REFUSAL_PHRASE}"
   (b) The question IS about Ark — a capability, integration, platform behaviour or
       supported tool — but the chunks contain nothing on it.
       Answer exactly: "{ROADMAP_PHRASE}"
   Deciding between them: if the question asks what Ark can do, supports, or integrates
   with, it is (b) even when the chunks say nothing. Only use (a) when the subject is not
   Ark, or when answering would undermine it. Never promise a roadmap for (a).
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
    """Which provider generates the answer: 'pi' or 'cursor'."""
    return os.environ.get("ANSWER_BACKEND", DEFAULT_BACKEND).strip().lower()


def _cursor_backend() -> str:
    """Which Cursor transport to use once the cursor provider is selected."""
    return os.environ.get("CURSOR_ANSWER_BACKEND", "auto").strip().lower()


def _pi_streaming_enabled() -> bool:
    """Real token streaming on the Pi backend. Set PI_STREAM=0 to fall back."""
    return os.environ.get("PI_STREAM", "1").strip().lower() not in ("0", "false", "no")


def _pi_request_params(model: str) -> dict[str, Any]:
    """Per-model request parameters, overridable with PI_EXTRA_PARAMS (JSON)."""
    params = dict(PI_MODEL_PARAMS.get(model, {}))
    raw = os.environ.get("PI_EXTRA_PARAMS", "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"PI_EXTRA_PARAMS is not valid JSON: {exc}") from exc
        if not isinstance(override, dict):
            raise ValueError("PI_EXTRA_PARAMS must be a JSON object")
        params.update(override)
    return params


# Transient gateway failures worth another attempt. Groq's JSON mode
# intermittently fails to produce valid JSON and answers 400 with
# failed_generation -- measured at roughly 5% under concurrency, which surfaced
# to users as a 502. It is a sampling failure, not a bad request, so the very
# same call succeeds on a retry.
_PI_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _pi_is_retryable(status: int, body: str) -> bool:
    if status in _PI_RETRYABLE_STATUS:
        return True
    if status == 400 and (
        "failed_generation" in body or "Failed to generate JSON" in body
    ):
        return True
    return False


def _call_pi_inference(prompt: str, *, model: str | None = None) -> str:
    """Generate via the Pi Inference gateway's OpenAI-compatible endpoint."""
    api_key = os.environ.get("PI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "PI_API_KEY not set. Set it, or set ANSWER_BACKEND=cursor to use the Cursor agent."
        )

    chosen = (model or os.environ.get("PI_MODEL") or PI_DEFAULT_MODEL).strip()
    base_url = os.environ.get("PI_BASE_URL", PI_DEFAULT_BASE_URL).rstrip("/")
    # 20s, not 60. Measured p95 on this workload is ~2.3s, so a request still
    # open at 20s is not coming back -- waiting a further 40s only turns a fast
    # failure into a hung request holding a worker thread.
    timeout = int(os.environ.get("PI_TIMEOUT_SECONDS", "20"))

    body: dict[str, Any] = {
        "model": chosen,
        "max_tokens": int(os.environ.get("PI_MAX_TOKENS", "800")),
        "messages": [{"role": "user", "content": prompt}],
    }
    body.update(_pi_request_params(chosen))

    attempts = max(1, int(os.environ.get("PI_MAX_ATTEMPTS", "3")))
    url = f"{base_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    detail = "unknown error"

    def _exhausted(message: str) -> str:
        """Say how many attempts ran, but only when more than one actually did.

        "on N attempt(s)", not "after": the timeout message already ends with
        "after {timeout}s", and two afters in one sentence reads as garbage.
        """
        return f"{message} on {attempt} attempt(s)" if attempt > 1 else message

    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            if last:
                raise TimeoutError(
                    _exhausted(f"Pi Inference timed out after {timeout}s")
                    + ". Retry or increase PI_TIMEOUT_SECONDS."
                ) from exc
            detail = f"timeout after {timeout}s"
        except httpx.HTTPError as exc:
            if last:
                raise RuntimeError(
                    _exhausted("Pi Inference request failed") + f": {exc}"
                ) from exc
            detail = str(exc)
        else:
            if response.status_code == 200:
                try:
                    text = response.json()["choices"][0]["message"]["content"]
                except (KeyError, IndexError, ValueError) as exc:
                    raise RuntimeError(
                        f"Pi Inference returned an unexpected payload: {response.text[:200]}"
                    ) from exc
                if text:
                    return str(text).strip()
                detail = "empty response"
                if last:
                    raise RuntimeError(_exhausted("Pi Inference returned an empty response"))
            else:
                detail = f"HTTP {response.status_code} {response.text[:200]}"
                if last or not _pi_is_retryable(response.status_code, response.text):
                    raise RuntimeError(
                        _exhausted(f"Pi Inference generation failed for model {chosen!r}")
                        + f": {detail}"
                    )
        # Exponential backoff, capped. Short because these are sampling and
        # rate-limit failures, not a service that needs time to come back.
        time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))

    raise RuntimeError(
        f"Pi Inference generation failed for model {chosen!r} on {attempts} attempt(s): {detail}"
    )


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


def _stream_cursor_agent_cli(prompt: str, *, model: str) -> Iterator[str]:
    """Stream stdout from the agent CLI as tokens arrive."""
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

    command = [
        agent_bin,
        "-p",
        "--mode",
        "ask",
        "--output-format",
        "text",
        "--trust",
        "--model",
        model,
        "--workspace",
        workspace,
        prompt,
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    raw_parts: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        assert proc.stdout is not None
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "Cursor generation timed out. Try again or increase CURSOR_TIMEOUT_SECONDS."
                )
            chunk = proc.stdout.read(256)
            if chunk:
                raw_parts.append(chunk)
                yield chunk
                continue
            if proc.poll() is not None:
                break
        if proc.returncode not in (0, None):
            err = proc.stderr.read() if proc.stderr is not None else ""
            raise RuntimeError(f"Cursor CLI stream failed: {err.strip() or 'unknown CLI error'}")
    finally:
        if proc.poll() is None:
            proc.kill()
        if proc.stderr is not None:
            proc.stderr.close()
        if proc.stdout is not None:
            proc.stdout.close()

    raw = "".join(raw_parts).strip()
    if not raw:
        raise RuntimeError("Cursor CLI returned an empty response")
    return raw


def _call_cursor_agent(prompt: str, *, model: str | None = None) -> str:
    """Generate via Cursor SDK (preferred) with agent CLI fallback."""
    backend = _cursor_backend()
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


def _call_model(prompt: str, *, model: str | None = None) -> str:
    """Generate the answer through whichever backend is configured."""
    backend = _answer_backend()
    if backend == "pi":
        return _call_pi_inference(prompt, model=model)
    if backend == "cursor":
        return _call_cursor_agent(prompt, model=model)
    raise ValueError(
        f"Unknown ANSWER_BACKEND {backend!r}; expected 'pi' or 'cursor'"
    )


def warm_agent() -> None:
    """Ping the Cursor agent CLI so the first user ask is not cold.

    The Pi Inference backend is a stateless HTTP call with nothing to warm.
    """
    if _answer_backend() != "cursor":
        return
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


def _build_user_content(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
) -> str:
    history_block = ""
    if history:
        formatted = _format_history(history)
        if formatted:
            history_block = (
                "Recent conversation (follow-up context only — "
                "still answer ONLY from the document chunks):\n"
                f"{formatted}\n\n"
            )

    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Document chunks:\n\n{_format_chunks(chunks)}\n\n"
        f"{history_block}"
        f"Question: {question}"
    )


def _generate_answer(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    user_content = _build_user_content(question, chunks, history=history)
    raw = _call_model(user_content, model=model)
    return _parse_and_finalize(raw, chunks)


def _finalize_parsed(
    parsed: dict[str, Any], chunks: list[dict[str, Any]]
) -> dict[str, Any]:
    answer_text = str(parsed.get("answer", "")).strip()
    citations = _resolve_citations(parsed, chunks)

    return {"answer": answer_text or REFUSAL_PHRASE, "citations": citations}


def _answer_text_from_partial_json(raw: str) -> str:
    """Best-effort extract of the answer string from incomplete JSON."""
    match = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
    if not match:
        return ""
    fragment = match.group(1)
    try:
        return json.loads(f'"{fragment}"')
    except json.JSONDecodeError:
        return (
            fragment.replace("\\n", "\n")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


class _AnswerFieldStreamer:
    """Map raw JSON token deltas to incremental answer-field text."""

    def __init__(self) -> None:
        self._raw = ""
        self._answer_text = ""

    def push(self, delta: str) -> str:
        self._raw += delta
        current = _answer_text_from_partial_json(self._raw)
        if not current.startswith(self._answer_text):
            # Model restarted or JSON shape shifted — emit the whole visible answer.
            new_text = current
        else:
            new_text = current[len(self._answer_text) :]
        self._answer_text = current
        return new_text


def _parse_and_finalize(raw: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        parsed = _parse_json_response(raw)
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        raise ValueError(f"Could not parse model response as JSON: {raw!r}") from exc
    return _finalize_parsed(parsed, chunks)


def _stream_cursor_sdk(prompt: str, *, model: str) -> Iterator[str]:
    """Yield text-delta tokens; return value is the full raw model response."""
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
    agent = Agent.create(
        AgentOptions(
            api_key=api_key,
            model=model,
            local=LocalAgentOptions(cwd=workspace),
        )
    )

    raw_parts: list[str] = []
    try:
        run = agent.send(prompt)
        last_assistant_text = ""
        for event in run.events():
            update = event.interaction_update
            if update is not None and getattr(update, "type", None) == "text-delta":
                text = getattr(update, "text", "")
                if text:
                    raw_parts.append(text)
                    yield text
                continue

            msg = event.sdk_message
            if msg is None or getattr(msg, "type", "") != "assistant":
                continue
            content = getattr(getattr(msg, "message", None), "content", ())
            block_text = "".join(getattr(block, "text", "") for block in content)
            if not block_text:
                continue
            if block_text.startswith(last_assistant_text):
                delta = block_text[len(last_assistant_text) :]
            else:
                delta = block_text
            last_assistant_text = block_text
            if delta:
                raw_parts.append(delta)
                yield delta

        result = run.wait()
        if getattr(result, "status", None) == "error":
            detail = getattr(result, "result", None) or "unknown SDK error"
            raise RuntimeError(f"Cursor SDK generation failed: {detail}")
        terminal = (getattr(result, "result", None) or "").strip()
        if terminal and not raw_parts:
            raw_parts.append(terminal)
            yield terminal
    finally:
        agent.close()

    return "".join(raw_parts).strip()


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


def _emit_streamed_raw(
    raw_chunks: Iterator[str],
    chunks: list[dict[str, Any]],
    *,
    stream_mode: str,
) -> Iterator[dict[str, Any]]:
    """Turn raw model chunks into answer-field delta events plus a done event."""
    answer_streamer = _AnswerFieldStreamer()
    raw = ""
    while True:
        try:
            delta = next(raw_chunks)
        except StopIteration as exc:
            raw = str(exc.value or "")
            break
        answer_delta = answer_streamer.push(delta)
        if answer_delta:
            yield {"type": "delta", "text": answer_delta}

    if not raw.strip():
        raise RuntimeError("Model stream returned an empty response")
    result = _parse_and_finalize(raw, chunks)
    yield {"type": "done", **result, "stream_mode": stream_mode}



def _stream_pi_inference(prompt: str, *, model: str | None = None) -> Iterator[str]:
    """Yield answer-text deltas from the gateway as the model produces them.

    Raises before yielding anything if the request cannot be established, so the
    caller can fall back cleanly. Once a delta has been emitted there is no safe
    retry -- the user has already seen text -- which is why the retry in
    _call_pi_inference lives on the blocking path and this one does not repeat.
    """
    api_key = os.environ.get("PI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("PI_API_KEY not set. Set it, or set ANSWER_BACKEND=cursor.")

    chosen = (model or os.environ.get("PI_MODEL") or PI_DEFAULT_MODEL).strip()
    base_url = os.environ.get("PI_BASE_URL", PI_DEFAULT_BASE_URL).rstrip("/")
    timeout = int(os.environ.get("PI_TIMEOUT_SECONDS", "20"))

    body: dict[str, Any] = {
        "model": chosen,
        "max_tokens": int(os.environ.get("PI_MAX_TOKENS", "800")),
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    params = _pi_request_params(chosen)
    # response_format=json_object turns streaming off at this gateway: the whole
    # answer arrives as a single frame. Measured 1 frame vs 58, and first-frame
    # 1294ms vs 614ms. The prompt already demands JSON and reasoning_effort=none
    # still suppresses the <think> trace, so drop only this one param here. If the
    # payload comes back unparseable the caller falls back to the blocking path,
    # which keeps JSON mode and its retry.
    params.pop("response_format", None)
    body.update(params)

    raw_parts: list[str] = []
    with httpx.stream(
        "POST",
        f"{base_url}/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    ) as response:
        if response.status_code != 200:
            response.read()
            raise RuntimeError(
                f"Pi Inference stream failed for model {chosen!r}: "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"].get("content")
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if not delta:
                continue
            raw_parts.append(delta)
            yield delta

    return "".join(raw_parts)


def stream_answer(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream grounded answer token deltas, then a final parsed result.

    Yields ``{"type": "delta", "text": "..."}`` events while the model
    generates, then ``{"type": "done", "answer": "...", "citations": [...]}``.
    Falls back to blocking ``answer()`` when SDK streaming is unavailable.
    """
    if not chunks:
        yield {"type": "done", "answer": REFUSAL_PHRASE, "citations": [], "stream_mode": "none"}
        return

    user_content = _build_user_content(question, chunks, history=history)
    errors: list[str] = []

    if _answer_backend() != "cursor":
        # Real token streaming first. If the request cannot be established we
        # have emitted nothing, so falling back to the blocking path is
        # invisible to the user and keeps its retry behaviour.
        if _pi_streaming_enabled():
            try:
                yield from _emit_streamed_raw(
                    _stream_pi_inference(user_content),
                    chunks,
                    stream_mode="pi-stream",
                )
                return
            except Exception as exc:  # noqa: BLE001 -- any transport or parse failure
                # Only safe because _emit_streamed_raw raises before its done
                # event; if deltas were already shown the fallback would repeat
                # them, so this relies on failures surfacing at connect or parse.
                errors.append(f"pi-stream: {exc}")

        result = _generate_answer(question, chunks, history=history)
        answer_text = str(result.get("answer", ""))
        if answer_text and not is_non_answer(answer_text):
            step = max(24, len(answer_text) // 40)
            for index in range(0, len(answer_text), step):
                yield {"type": "delta", "text": answer_text[index : index + step]}
        yield {
            "type": "done",
            **result,
            "stream_mode": "blocking-chunked",
            "stream_errors": errors,
        }
        return

    cursor_backend = _cursor_backend()
    models = _model_candidates(None)

    streamers: list[tuple[str, Any]] = []
    if cursor_backend in ("auto", "sdk"):
        streamers.append(("sdk", _stream_cursor_sdk))
    if cursor_backend in ("auto", "cli"):
        streamers.append(("cli", _stream_cursor_agent_cli))

    for chosen_model in models:
        for mode_name, stream_fn in streamers:
            try:
                yield from _emit_streamed_raw(
                    stream_fn(user_content, model=chosen_model),
                    chunks,
                    stream_mode=mode_name,
                )
                return
            except Exception as exc:
                errors.append(f"{mode_name}/{chosen_model}: {exc}")
        if errors and "Cannot use this model" not in " ".join(errors):
            break

    result = _generate_answer(question, chunks, history=history)
    answer_text = str(result.get("answer", ""))
    if answer_text and not is_non_answer(answer_text):
        step = max(24, len(answer_text) // 40)
        for index in range(0, len(answer_text), step):
            yield {"type": "delta", "text": answer_text[index : index + step]}
    yield {
        "type": "done",
        **result,
        "stream_mode": "blocking-chunked",
        "stream_errors": errors,
    }
