"""Scope router locked to the CI refusal sets."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.scope_router import named_team_missing_from_chunks, should_refuse

HOLDOUT_PATH = Path(__file__).resolve().parent.parent / "eval" / "router-holdout-questions.json"
QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "eval" / "questions.json"
GUARDRAIL_PATH = Path(__file__).resolve().parent.parent / "eval" / "guardrail-questions.json"

_NAMED_TEAM_IDS = {"gr-adj-other-team"}


def _pre_refused(question: str) -> bool:
    if should_refuse(question):
        return True
    return named_team_missing_from_chunks(
        question, [{"text": "none of that name", "source": "x"}]
    )


class ScopeRouterHoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
        cls.questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
        cls.guardrail = json.loads(GUARDRAIL_PATH.read_text(encoding="utf-8"))
        cls.scored = [item for item in cls.questions if item.get("expected_source")]

    def test_every_ci_refusal_is_pre_refused(self) -> None:
        """guardrail-eval + refusal-eval rows must not reach the model."""
        rows = [
            ("guardrail", item)
            for item in self.guardrail
            if item.get("expect_refusal")
        ] + [
            ("questions", item)
            for item in self.questions
            if item.get("expect_refusal")
        ]
        for source, item in rows:
            with self.subTest(source=source, item=item["id"]):
                self.assertTrue(
                    _pre_refused(str(item["question"])),
                    msg=item["question"],
                )

    def test_scored_questions_never_pre_refused(self) -> None:
        for item in self.scored:
            with self.subTest(item=item["id"]):
                self.assertFalse(
                    should_refuse(str(item["question"])),
                    msg=item["question"],
                )

    def test_holdout_matches_expect_refusal(self) -> None:
        for item in self.holdout:
            question = str(item["question"])
            expected = bool(item["expect_refusal"])
            with self.subTest(item=item["id"]):
                self.assertEqual(
                    should_refuse(question),
                    expected,
                    msg=item.get("why", question),
                )

    def test_documented_system_prompt_questions_pass(self) -> None:
        for question in (
            "how do I set the system_prompt on an agent",
            "what does system prompt mean in Ark",
            "show the setup instructions for Cursor",
            "display the system_prompt field in the agent yaml",
        ):
            with self.subTest(question=question):
                self.assertFalse(should_refuse(question), msg=question)

    def test_must_answer_canaries_pass(self) -> None:
        for question in (
            "how do I list all the api keys for my workspace",
            "can I push to main without waiting for CI",
            "what workspaces does the platform team have access to?",
            "how do I create a new jira board link in my flow?",
            "how do I register compute in my AWS account for Ark",
            "what is on the Ark roadmap",
        ):
            with self.subTest(question=question):
                self.assertFalse(should_refuse(question), msg=question)

    def test_named_team_other_team_is_post_retrieve(self) -> None:
        item = next(row for row in self.guardrail if row["id"] in _NAMED_TEAM_IDS)
        question = str(item["question"])
        self.assertFalse(should_refuse(question))
        self.assertTrue(
            named_team_missing_from_chunks(
                question,
                [{"text": "use ark workspace list", "source": "first-run"}],
            )
        )
        self.assertFalse(
            named_team_missing_from_chunks(
                question,
                [{"text": "the lending team applies its own workspace", "source": "admin"}],
            )
        )

    def test_platform_team_is_a_known_actor(self) -> None:
        self.assertFalse(
            named_team_missing_from_chunks(
                "what workspaces does the platform team have access to?",
                [{"text": "use ark workspace list", "source": "first-run"}],
            )
        )

    def test_my_team_inventory_is_not_a_named_team_miss(self) -> None:
        self.assertFalse(
            named_team_missing_from_chunks(
                "what workspaces does my team have",
                [{"text": "ark workspace list", "source": "first-run"}],
            )
        )


if __name__ == "__main__":
    unittest.main()
