"""End-to-end ask() and interactive CLI for the onboarding bot."""

from __future__ import annotations

import argparse
import sys
import warnings
from typing import IO, Any

from src.answer import REFUSAL_PHRASE, answer
from src.retrieve import retrieve

try:
    from src.retriever import DEFAULT_TOP_K
except ImportError:
    DEFAULT_TOP_K = 8

EXIT_COMMANDS = {"quit", "exit", "q"}


def ask(
    question: str,
    *,
    k: int | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Retrieve relevant chunks, then generate a grounded answer."""
    question = question.strip()
    if not question:
        return {"answer": REFUSAL_PHRASE, "citations": [], "retrieved_sources": []}

    top_k = DEFAULT_TOP_K if k is None else k
    chunks = retrieve(question, k=top_k)
    if not chunks:
        return {"answer": REFUSAL_PHRASE, "citations": [], "retrieved_sources": []}

    result = answer(question, chunks, history=history)
    result["retrieved_sources"] = [
        str(chunk["source"]) for chunk in chunks if chunk.get("source")
    ]
    return result


def run_question(
    question: str,
    *,
    k: int | None = None,
    history: list[dict[str, str]] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run retrieve → answer with optional progress messages for the CLI."""
    question = question.strip()
    if not question:
        return {"answer": REFUSAL_PHRASE, "citations": [], "retrieved_sources": []}

    top_k = DEFAULT_TOP_K if k is None else k

    if verbose:
        print("Retrieving relevant docs...", flush=True)

    chunks = retrieve(question, k=top_k)
    if not chunks:
        return {"answer": REFUSAL_PHRASE, "citations": [], "retrieved_sources": []}

    if verbose:
        print("Generating answer (may take 1–2 min)...", flush=True)

    result = answer(question, chunks, history=history)
    result["retrieved_sources"] = [
        str(chunk["source"]) for chunk in chunks if chunk.get("source")
    ]
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
