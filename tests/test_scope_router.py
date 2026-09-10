"""Scope router: jailbreak shape plus structural out-of-scope / inventory."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.scope_router import named_team_missing_from_chunks, should_refuse

HOLDOUT_PATH = Path(__file__).resolve().parent.parent / "eval" / "router-holdout-questions.json"
SCORED_PATH = Path(__file__).resolve().parent.parent / "eval" / "questions.json"
GUARDRAIL_PATH = Path(__file__).resolve().parent.parent / "eval" / "guardrail-questions.json"


class ScopeRouterHoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
        cls.scored = [
            item
            for item in json.loads(SCORED_PATH.read_text(encoding="utf-8"))
            if item.get("expected_source")
        ]
        cls.guardrail = json.loads(GUARDRAIL_PATH.read_text(encoding="utf-8"))

    def _rows(self, prefix: str) -> list[dict]:
        return [item for item in self.holdout if str(item["id"]).startswith(prefix)]

    def test_jailbreak_holdout_refused(self) -> None:
        for item in self._rows("hold-inj-"):
            with self.subTest(item=item["id"]):
                self.assertTrue(
                    should_refuse(str(item["question"])),
                    msg=item.get("why", item["question"]),
                )

    def test_legitimate_holdout_passes(self) -> None:
        for item in self._rows("hold-ok-"):
            with self.subTest(item=item["id"]):
                self.assertFalse(
                    should_refuse(str(item["question"])),
                    msg=item.get("why", item["question"]),
                )

    def test_scope_and_enumeration_holdout_refused(self) -> None:
        for item in self._rows("hold-oos-") + self._rows("hold-sec-"):
            with self.subTest(item=item["id"]):
                self.assertTrue(
                    should_refuse(str(item["question"])),
                    msg=item.get("why", item["question"]),
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

    def test_scored_questions_never_pre_refused(self) -> None:
        for item in self.scored:
            with self.subTest(item=item["id"]):
                self.assertFalse(
                    should_refuse(str(item["question"])),
                    msg=item["question"],
                )

    def test_guardrail_rows_router_or_named_team(self) -> None:
        """expect_refusal rows are pre-refused, named-team, or a dated-roadmap ask."""
        named_team_ids = {"gr-adj-other-team"}
        # Same shape as hold-ok-01/02 (roadmap page exists). The model already
        # refuses a date that is not in the chunks; do not regex "when will X ship".
        model_ids = {"gr-adj-roadmap-date"}
        for item in self.guardrail:
            if not item.get("expect_refusal") or item["id"] in model_ids:
                continue
            question = str(item["question"])
            if item["id"] in named_team_ids:
                with self.subTest(item=item["id"]):
                    self.assertFalse(should_refuse(question), msg=question)
                    self.assertTrue(
                        named_team_missing_from_chunks(
                            question,
                            [{"text": "use ark workspace list", "source": "first-run"}],
                        ),
                        msg=question,
                    )
                continue
            with self.subTest(item=item["id"]):
                self.assertTrue(should_refuse(question), msg=question)

    def test_canaries_near_guardrail_misses_pass(self) -> None:
        for question in (
            "my postgres query is slow inside a flow, is that an Ark limit",
            "how do I list all the api keys for my workspace",
            "can I push to main without waiting for CI",
            "what workspaces does the platform team have",
        ):
            with self.subTest(question=question):
                self.assertFalse(should_refuse(question), msg=question)

    def test_named_team_missing_when_chunks_omit_the_name(self) -> None:
        question = "what workspaces does the lending team have configured"
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

    def test_platform_team_present_in_chunks_is_not_missing(self) -> None:
        self.assertFalse(
            named_team_missing_from_chunks(
                "what workspaces does the platform team have",
                [{"text": "the platform team creates the accounts", "source": "getting-started"}],
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
