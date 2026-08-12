#!/usr/bin/env python3
"""Manual smoke test for the grounded answer layer."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from src.answer import REFUSAL_PHRASE, answer
from src.retriever import retrieve

QUESTIONS = [
    "how do I enroll a host?",
    "how do I set up Cursor?",
    "which model should I use?",
    "how do I reset my Jira password?",
]


def main() -> int:
    load_dotenv()

    print("Running answer layer smoke test\n")
    print("-" * 72)

    for question in QUESTIONS:
        print(f"\nQ: {question}")
        try:
            result = answer(question, retrieve(question, top_k=5))
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1

        print(f"A: {result['answer']}")
        print(f"Citations: {result['citations']}")

        if question == QUESTIONS[-1] and result["answer"] != REFUSAL_PHRASE:
            print("\nExpected refusal for out-of-scope question.")
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
