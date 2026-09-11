"""Tests for onboarding vs session-debug intent routing."""

from __future__ import annotations

import unittest

from src.intent_router import extract_ark_session_id, resolve_intent


class IntentRouterTests(unittest.TestCase):
    def test_bare_session_id(self) -> None:
        self.assertEqual(extract_ark_session_id("s-abc1234567"), "s-abc1234567")

    def test_session_id_in_sentence(self) -> None:
        self.assertEqual(
            extract_ark_session_id("debug session s-abc1234567 please"),
            "s-abc1234567",
        )

    def test_debug_prefix(self) -> None:
        self.assertEqual(
            extract_ark_session_id("debug s-abc1234567"),
            "s-abc1234567",
        )

    def test_onboarding_question(self) -> None:
        intent, sid = resolve_intent("how do I enroll a host?")
        self.assertEqual(intent, "onboarding")
        self.assertIsNone(sid)

    def test_session_debug_intent(self) -> None:
        intent, sid = resolve_intent("s-abc1234567")
        self.assertEqual(intent, "session_debug")
        self.assertEqual(sid, "s-abc1234567")

    def test_debug_thread_without_id(self) -> None:
        intent, sid = resolve_intent("why did it fail?", debug_thread=True)
        self.assertEqual(intent, "session_debug")
        self.assertIsNone(sid)


if __name__ == "__main__":
    unittest.main()
