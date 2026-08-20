"""End-to-end ask() and interactive CLI for the onboarding bot."""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from collections import OrderedDict
from typing import IO, Any

from src.answer import REFUSAL_PHRASE, answer
from src.query_log import log_query
from src.retrieve import RetrievalResult, retrieve_scored

try:
    from src.retriever import DEFAULT_TOP_K
except ImportError:
    DEFAULT_TOP_K = 8

# The answer path retrieves fewer chunks than eval for a shorter prompt / faster
# generation. Override with ASK_TOP_K (higher = more context but slower).
# k=8 keeps weaker models from refusing when key chunks rank ~4th (e.g. enroll-a-host).
DEFAULT_ASK_TOP_K = 8


def _default_top_k() -> int:
    raw = os.environ.get("ASK_TOP_K")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_ASK_TOP_K


EXIT_COMMANDS = {"quit", "exit", "q"}

_RETRIEVAL_CACHE: OrderedDict[tuple[str, int], RetrievalResult] = OrderedDict()
_ANSWER_CACHE: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()


def _cache_enabled() -> bool:
    raw = os.environ.get("ASK_RETRIEVAL_CACHE", "1").strip().lower()
    return raw not in ("0", "false", "no")


def _cache_max_size() -> int:
    raw = os.environ.get("ASK_RETRIEVAL_CACHE_SIZE", "128")
    try:
        return max(0, int(raw))
    except ValueError:
        return 128


def _normalize_cache_key(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return normalized.rstrip("?.!")


def clear_retrieval_cache() -> None:
    """Clear the in-process retrieval LRU (for tests)."""
    _RETRIEVAL_CACHE.clear()


def clear_answer_cache() -> None:
    """Clear the in-process answer LRU (for tests)."""
    _ANSWER_CACHE.clear()


def _answer_cache_enabled() -> bool:
    raw = os.environ.get("ASK_ANSWER_CACHE", "1").strip().lower()
    return raw not in ("0", "false", "no")


def _answer_cache_max_size() -> int:
    raw = os.environ.get("ASK_ANSWER_CACHE_SIZE", "128")
    try:
        return max(0, int(raw))
    except ValueError:
        return 128


def _answer_cache_key(question: str, k: int) -> tuple[str, int]:
    return (_normalize_cache_key(question), k)


def _get_cached_answer(question: str, k: int) -> dict[str, Any] | None:
    if not _answer_cache_enabled():
        return None
    if _answer_cache_max_size() == 0:
        return None

    key = _answer_cache_key(question, k)
    cached = _ANSWER_CACHE.get(key)
    if cached is None:
        return None
    _ANSWER_CACHE.move_to_end(key)
    return dict(cached)


def _store_cached_answer(question: str, k: int, result: dict[str, Any]) -> None:
    if not _answer_cache_enabled():
        return
    max_size = _answer_cache_max_size()
    if max_size == 0:
        return

    key = _answer_cache_key(question, k)
    _ANSWER_CACHE[key] = {
        "answer": str(result.get("answer", "")),
        "citations": [str(item) for item in result.get("citations", [])],
        "retrieved_sources": [str(item) for item in result.get("retrieved_sources", [])],
        "top_score": result.get("top_score"),
        "chunk_count": int(result.get("chunk_count", 0)),
    }
    _ANSWER_CACHE.move_to_end(key)
    while len(_ANSWER_CACHE) > max_size:
        _ANSWER_CACHE.popitem(last=False)


def _log_ask_result(
    question: str,
    result: dict[str, Any],
    *,
    channel: str,
    session_id: str | None,
) -> None:
    log_query(
        question=question,
        answer=str(result.get("answer", "")),
        citations=[str(item) for item in result.get("citations", [])],
        retrieved_sources=[str(item) for item in result.get("retrieved_sources", [])],
        top_score=result.get("top_score"),
        chunk_count=int(result.get("chunk_count", 0)),
        channel=channel,
        session_id=session_id,
    )


def _cached_retrieve_scored(query: str, *, k: int) -> RetrievalResult:
    if not _cache_enabled():
        return retrieve_scored(query, k=k)

    max_size = _cache_max_size()
    if max_size == 0:
        return retrieve_scored(query, k=k)

    key = (_normalize_cache_key(query), k)
    cached = _RETRIEVAL_CACHE.get(key)
    if cached is not None:
        _RETRIEVAL_CACHE.move_to_end(key)
        return cached

    result = retrieve_scored(query, k=k)
    _RETRIEVAL_CACHE[key] = result
    _RETRIEVAL_CACHE.move_to_end(key)
    while len(_RETRIEVAL_CACHE) > max_size:
        _RETRIEVAL_CACHE.popitem(last=False)
    return result


def _retrieval_query(question: str, history: list[dict[str, str]] | None = None) -> str:
    """Build a retrieval query that includes recent user questions for follow-ups."""
    if not history:
        return question

    prior_questions = [
        str(turn.get("question", "")).strip()
        for turn in history
        if str(turn.get("question", "")).strip()
    ]
    if not prior_questions:
        return question

    # Follow-ups often omit the topic ("where does the config file go?") — keep
    # recent user questions in the embed query so retrieval stays on-topic.
    context = " ".join(prior_questions[-2:])
    return f"{context} {question}".strip()


def _result_from_retrieval(
    question: str,
    scored,
    *,
    history: list[dict[str, str]] | None,
) -> dict[str, Any]:
    chunks = scored.chunks
    top_score = scored.top_score
    chunk_count = len(chunks)
    if not chunks:
        return {
            "answer": REFUSAL_PHRASE,
            "citations": [],
            "retrieved_sources": [],
            "top_score": top_score,
            "chunk_count": chunk_count,
        }

    result = answer(question, chunks, history=history)
    result["retrieved_sources"] = [
        str(chunk["source"]) for chunk in chunks if chunk.get("source")
    ]
    result["top_score"] = top_score
    result["chunk_count"] = chunk_count
    return result


def ask(
    question: str,
    *,
    k: int | None = None,
    history: list[dict[str, str]] | None = None,
    channel: str = "cli",
    session_id: str | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Retrieve relevant chunks, then generate a grounded answer."""
    question = question.strip()
    if not question:
        return {
            "answer": REFUSAL_PHRASE,
            "citations": [],
            "retrieved_sources": [],
            "top_score": None,
            "chunk_count": 0,
        }

    top_k = _default_top_k() if k is None else k

    if not history:
        cached = _get_cached_answer(question, top_k)
        if cached is not None:
            if log:
                _log_ask_result(
                    question, cached, channel=channel, session_id=session_id
                )
            return cached

    scored = _cached_retrieve_scored(_retrieval_query(question, history), k=top_k)
    result = _result_from_retrieval(question, scored, history=history)
    if not history and scored.chunks:
        _store_cached_answer(question, top_k, result)
    if log:
        _log_ask_result(question, result, channel=channel, session_id=session_id)
    return result


def run_question(
    question: str,
    *,
    k: int | None = None,
    history: list[dict[str, str]] | None = None,
    verbose: bool = True,
    channel: str = "cli",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Run retrieve → answer with optional progress messages for the CLI."""
    question = question.strip()
    if not question:
        return ask("", channel=channel, session_id=session_id)

    top_k = _default_top_k() if k is None else k

    if not history:
        cached = _get_cached_answer(question, top_k)
        if cached is not None:
            _log_ask_result(question, cached, channel=channel, session_id=session_id)
            return cached

    if verbose:
        print("Retrieving relevant docs...", flush=True)

    scored = _cached_retrieve_scored(_retrieval_query(question, history), k=top_k)
    if not scored.chunks:
        result = {
            "answer": REFUSAL_PHRASE,
            "citations": [],
            "retrieved_sources": [],
            "top_score": scored.top_score,
            "chunk_count": 0,
        }
        _log_ask_result(question, result, channel=channel, session_id=session_id)
        return result

    if verbose:
        print("Generating answer (may take 1–2 min)...", flush=True)

    result = _result_from_retrieval(question, scored, history=history)
    if not history:
        _store_cached_answer(question, top_k, result)
    _log_ask_result(question, result, channel=channel, session_id=session_id)
    return result


def print_result(result: dict[str, Any], *, file: IO[str] = sys.stdout) -> None:
    """Print a grounded answer and its citations in a readable CLI format."""
    answer_text = str(result.get("answer", "")).strip() or REFUSAL_PHRASE
    citations_raw = result.get("citations", [])
    citations = citations_raw if isinstance(citations_raw, list) else []

    if answer_text == REFUSAL_PHRASE:
        print("\n--- Not in the docs ---", file=file)
        print(f"\n{answer_text}\n", file=file)
        return

    print("\n--- Answer ---", file=file)
    print(f"\n{answer_text}\n", file=file)

    if citations:
        print("Sources:", file=file)
        for index, citation in enumerate(citations, start=1):
            print(f"  {index}. {citation}", file=file)
    print(file=file)


def _suppress_noisy_warnings() -> None:
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"src\.retriever")


def _handle_error(exc: Exception) -> int:
    if isinstance(exc, ValueError) and "CURSOR_API_KEY" in str(exc):
        print(
            "Error: CURSOR_API_KEY is not set.\n"
            "Add it to .env or run: export CURSOR_API_KEY=...\n",
            file=sys.stderr,
        )
        return 1
    if isinstance(exc, (TimeoutError, RuntimeError, ValueError)):
        print(f"Error: {exc}\n", file=sys.stderr)
        return 1
    raise exc


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    _suppress_noisy_warnings()

    parser = argparse.ArgumentParser(
        description="Ask Ark onboarding questions — grounded in docs with citations.",
    )
    parser.add_argument(
        "question",
        nargs="*",
        help='Question to ask. Omit for interactive mode. Example: "how do I enroll a host?"',
    )
    args = parser.parse_args(argv)

    if args.question:
        question = " ".join(args.question).strip()
        if not question:
            print("Error: empty question.\n", file=sys.stderr)
            return 1
        try:
            print_result(run_question(question))
        except (RuntimeError, TimeoutError, ValueError) as exc:
            return _handle_error(exc)
        return 0

    print("Ark onboarding bot")
    print("Ask onboarding questions — answers come from the docs with citations.")
    print("Commands: quit, exit, q\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not question:
            print("(type a question, or quit to exit)\n")
            continue

        if question.lower() in EXIT_COMMANDS:
            return 0

        try:
            print_result(run_question(question))
        except (RuntimeError, TimeoutError, ValueError) as exc:
            _handle_error(exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
