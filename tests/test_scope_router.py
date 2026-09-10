"""Scope router: structural jailbreak only; scope/enumeration stay with the model."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.scope_router import should_refuse

HOLDOUT_PATH = Path(__file__).resolve().parent.parent / "eval" / "router-holdout-questions.json"
SCORED_PATH = Path(__file__).resolve().parent.parent / "eval" / "questions.json"


class ScopeRouterHoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
        cls.scored = [
            item
            for item in json.loads(SCORED_PATH.read_text(encoding="utf-8"))
            if item.get("expected_source")
        ]

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

    def test_scope_and_enumeration_not_router_blocked(self) -> None:
        """OOS/SEC rows in the holdout are model-level refusals, not pre-retrieval."""
        for item in self._rows("hold-oos-") + self._rows("hold-sec-"):
            with self.subTest(item=item["id"]):
                self.assertFalse(
                    should_refuse(str(item["question"])),
                    msg=item.get("why", item["question"]),
                )

    def test_scored_questions_never_pre_refused(self) -> None:
        for item in self.scored:
            with self.subTest(item=item["id"]):
                self.assertFalse(
                    should_refuse(str(item["question"])),
                    msg=item["question"],
                )


if __name__ == "__main__":
    unittest.main()
