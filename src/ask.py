"""End-to-end ask() and interactive CLI for the onboarding bot."""

from __future__ import annotations

import sys

from src.answer import REFUSAL_PHRASE, answer
from src.retrieve import retrieve


def ask(question: str, *, k: int = 5) -> dict[str, list[str] | str]:
    """Retrieve relevant chunks, then generate a grounded answer."""
    question = question.strip()
    if not question:
        return {"answer": REFUSAL_PHRASE, "citations": []}

    chunks = retrieve(question, k=k)
    if not chunks:
        return {"answer": REFUSAL_PHRASE, "citations": []}

    return answer(question, chunks)


def _print_result(result: dict[str, list[str] | str]) -> None:
    print(f"\nAnswer:\n{result['answer']}\n")
    citations = result.get("citations", [])
    if citations:
        print("Citations:")
        for citation in citations:
            print(f"  - {citation}")
    print()


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    print("Ark onboarding bot")
    print("Type a question and press Enter. Use quit, exit, or Ctrl+C to leave.\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question or question.lower() in {"quit", "exit", "q"}:
            break

        try:
            _print_result(ask(question))
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"Error: {exc}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
