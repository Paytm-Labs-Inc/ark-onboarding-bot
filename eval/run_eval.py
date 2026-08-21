#!/usr/bin/env python3
"""Run gold-set eval: retrieval hit-rate and (optionally) citation accuracy."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class QuestionResult:
    id: str
    question: str
    expected_source: str | None
    expect_refusal: bool
    retrieval_hit: bool
    retrieved_sources: list[str]
    citation_hit: bool | None
    citations: list[str]
    answer_preview: str | None
    error: str | None = None


def norm_source(value: str) -> str:
    """Normalize corpus source labels for comparison."""
    stem = value.split(" -- ", 1)[0].strip().lower()
    return stem


def source_matches(expected: str, actual: str) -> bool:
    return norm_source(actual) == expected.strip().lower()


def any_source_matches(expected: str, sources: list[str]) -> bool:
    return any(source_matches(expected, source) for source in sources)


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected a JSON list")
    return payload


def filter_questions(
    questions: list[dict],
    *,
    only_refusals: bool,
    only_scored: bool,
) -> list[dict]:
    if only_refusals and only_scored:
        raise ValueError("Use at most one of --only-refusals and --only-scored")
    if only_refusals:
        return [item for item in questions if item.get("expect_refusal")]
    if only_scored:
        return [item for item in questions if item.get("expected_source") is not None]
    return questions


def evaluate_question(
    item: dict,
    *,
    top_k: int,
    run_answer: bool,
) -> QuestionResult:
    from src.answer import is_non_answer
    from src.ask import ask
    from src.retrieve import retrieve

    question = str(item["question"])
    expected = item.get("expected_source")
    expect_refusal = bool(item.get("expect_refusal", False))

    try:
        chunks = retrieve(question, k=top_k)
        retrieved_sources = [str(chunk.get("source", "")) for chunk in chunks]
        retrieval_hit = (
            expected is not None and any_source_matches(str(expected), retrieved_sources)
        )

        citation_hit: bool | None = None
        citations: list[str] = []
        answer_preview: str | None = None

        if run_answer:
            result = ask(question, k=top_k, log=False, channel="eval")
            answer_preview = str(result.get("answer", ""))[:240]
            raw_citations = result.get("citations", [])
            citations = [str(item) for item in raw_citations] if isinstance(raw_citations, list) else []

            if expect_refusal:
                citation_hit = is_non_answer(str(result.get("answer", ""))) and not citations
            elif expected is not None:
                citation_hit = any_source_matches(str(expected), citations)
            else:
                citation_hit = False

        return QuestionResult(
            id=str(item.get("id", question)),
            question=question,
            expected_source=str(expected) if expected is not None else None,
            expect_refusal=expect_refusal,
            retrieval_hit=retrieval_hit,
            retrieved_sources=retrieved_sources,
            citation_hit=citation_hit,
            citations=citations,
            answer_preview=answer_preview,
        )
    except Exception as exc:  # noqa: BLE001 — eval runner should keep going
        return QuestionResult(
            id=str(item.get("id", question)),
            question=question,
            expected_source=str(expected) if expected is not None else None,
            expect_refusal=expect_refusal,
            retrieval_hit=False,
            retrieved_sources=[],
            citation_hit=None if not run_answer else False,
            citations=[],
            answer_preview=None,
            error=str(exc),
        )


def _pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def print_report(
    results: list[QuestionResult],
    *,
    run_answer: bool,
    top_k: int,
    max_chars: int,
    model_name: str,
) -> None:
    scored = [r for r in results if r.expected_source is not None]
    refusals = [r for r in results if r.expect_refusal]

    retrieval_pass = sum(1 for r in scored if r.retrieval_hit)
    print("\nEval summary")
    print("=" * 72)
    print(
        f"Config: MAX_CHARS={max_chars}, top_k={top_k}, model={model_name}",
    )
    print(
        f"Retrieval hit @ top-k: {retrieval_pass}/{len(scored)} "
        f"({_pct(retrieval_pass, len(scored))}%)"
    )

    if run_answer:
        citation_scored = [r for r in scored if r.citation_hit is not None]
        citation_pass = sum(1 for r in citation_scored if r.citation_hit)
        print(
            f"Citation hit:          {citation_pass}/{len(citation_scored)} "
            f"({_pct(citation_pass, len(citation_scored))}%)"
        )

        refusal_pass = sum(1 for r in refusals if r.citation_hit)
        print(
            f"Refusal cases:         {refusal_pass}/{len(refusals)} "
            f"({_pct(refusal_pass, len(refusals))}%)"
        )

    print("\nDetails")
    print("-" * 72)
    for result in results:
        if result.expect_refusal:
            status = (
                "PASS"
                if run_answer and result.citation_hit
                else "REFUSAL" if run_answer else "SKIP"
            )
        elif result.retrieval_hit:
            status = "PASS"
        else:
            status = "MISS"
        if result.error:
            status = "ERROR"
        print(f"[{status}] {result.id}: {result.question}")
        if result.expected_source:
            print(f"  expected: {result.expected_source}")
        if result.retrieved_sources:
            preview = ", ".join(norm_source(s) for s in result.retrieved_sources[:3])
            print(f"  retrieved (top): {preview}")
        if run_answer and result.answer_preview is not None:
            print(f"  answer: {result.answer_preview}")
            if result.citations:
                print(f"  citations: {', '.join(norm_source(c) for c in result.citations)}")
        if result.error:
            print(f"  error: {result.error}")
        print()


def write_report(
    results: list[QuestionResult],
    *,
    run_answer: bool,
    top_k: int,
    max_chars: int,
    model_name: str,
) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"eval-{stamp}.json"
    payload = {
        "generated_at": stamp,
        "run_answer": run_answer,
        "config": {
            "max_chars": max_chars,
            "top_k": top_k,
            "model_name": model_name,
        },
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run onboarding bot eval gold set")
    parser.add_argument(
        "--questions",
        type=Path,
        default=QUESTIONS_PATH,
        help="Path to questions JSON (default: eval/questions.json)",
    )
    from src.chunker import MAX_CHARS
    from src.retriever import DEFAULT_TOP_K, MODEL_NAME

    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Retrieval top-k (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run full ask() including Cursor generation (needs CURSOR_API_KEY)",
    )
    parser.add_argument(
        "--quiet-retriever",
        action="store_true",
        help="Suppress retriever timing logs during eval",
    )
    parser.add_argument(
        "--only-refusals",
        action="store_true",
        help="Run only expect_refusal questions (requires --full to assert refusal)",
    )
    parser.add_argument(
        "--only-scored",
        action="store_true",
        help="Run only in-scope retrieval questions (expected_source set)",
    )
    args = parser.parse_args(argv)

    if args.only_refusals and not args.full:
        print(
            "Refusal cases require --full to assert clean refusals via ask().",
            file=sys.stderr,
        )
        return 2

    if args.full:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

    if args.quiet_retriever:
        import builtins

        original_print = builtins.print

        def filtered_print(*values: object, **kwargs: object) -> None:
            message = " ".join(str(value) for value in values)
            if message.startswith("retrieved ") and "chunks in" in message:
                return
            original_print(*values, **kwargs)

        builtins.print = filtered_print

    questions = filter_questions(
        load_questions(args.questions),
        only_refusals=args.only_refusals,
        only_scored=args.only_scored,
    )
    if not questions:
        print("No questions selected for this eval run.", file=sys.stderr)
        return 2

    if args.full:
        scope = "refusal" if args.only_refusals else "full"
        print(
            f"Running {scope} eval on {len(questions)} questions "
            "(each calls Cursor — expect several minutes).",
            flush=True,
        )
    elif args.only_scored:
        print(
            f"Running retrieval eval on {len(questions)} in-scope questions.",
            flush=True,
        )

    results: list[QuestionResult] = []
    for index, item in enumerate(questions, start=1):
        if args.full:
            qid = str(item.get("id", item.get("question", index)))
            print(f"[{index}/{len(questions)}] {qid} ...", flush=True)
        results.append(
            evaluate_question(item, top_k=args.top_k, run_answer=args.full)
        )
        if args.full:
            last = results[-1]
            status = "ok" if not last.error else f"error: {last.error}"
            print(f"    done ({status})", flush=True)
    print_report(
        results,
        run_answer=args.full,
        top_k=args.top_k,
        max_chars=MAX_CHARS,
        model_name=MODEL_NAME,
    )
    report_path = write_report(
        results,
        run_answer=args.full,
        top_k=args.top_k,
        max_chars=MAX_CHARS,
        model_name=MODEL_NAME,
    )
    print(f"Wrote report: {report_path}")

    scored = [r for r in results if r.expected_source is not None]
    refusals = [r for r in results if r.expect_refusal]

    if args.only_refusals:
        refusal_pass = sum(1 for r in refusals if r.citation_hit)
        if refusal_pass < len(refusals):
            return 1
        return 0

    retrieval_pass = sum(1 for r in scored if r.retrieval_hit)
    if retrieval_pass < len(scored):
        return 1
    if args.full:
        citation_scored = [r for r in scored if r.citation_hit is not None]
        citation_pass = sum(1 for r in citation_scored if r.citation_hit)
        refusal_pass = sum(1 for r in refusals if r.citation_hit)
        if citation_pass < len(citation_scored) or refusal_pass < len(refusals):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
