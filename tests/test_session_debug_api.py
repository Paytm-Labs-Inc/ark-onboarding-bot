"""Tests for session debug gate approve/reject API."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.web import app


class SessionDebugApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._token = os.environ.pop("ARK_ACCESS_TOKEN", None)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        if self._token is not None:
            os.environ["ARK_ACCESS_TOKEN"] = self._token
        else:
            os.environ.pop("ARK_ACCESS_TOKEN", None)

    @patch("src.web.approve_plan")
    def test_approve_gate(self, mock_approve) -> None:
        from src.session_debug import DebugResult

        mock_approve.return_value = DebugResult(
            answer="Dispatched.",
            case="needs_fix",
            dispatch_session_id="s-dispatch1",
        )
        response = self.client.post(
            "/api/session-debug/approve",
            json={"gate_id": "gate123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Dispatched", response.json()["answer"])

    @patch("src.web.reject_plan")
    def test_reject_gate(self, mock_reject) -> None:
        from src.session_debug import DebugResult

        mock_reject.return_value = DebugResult(answer="Rejected.", case="needs_fix")
        response = self.client.post(
            "/api/session-debug/reject",
            json={"gate_id": "gate123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Rejected", response.json()["answer"])

    @patch("src.web.run_debug_action")
    def test_debug_action_endpoint(self, mock_action) -> None:
        from src.session_debug import DebugResult

        mock_action.return_value = DebugResult(
            answer="Ticket draft here.",
            case="cannot_fix",
            gate_kind="next_steps",
            gate_actions=[{"id": "slack_message", "label": "Generate #foundry-users message"}],
            gate_pending=True,
        )
        response = self.client.post(
            "/api/session-debug/action",
            json={"gate_id": "gate123", "action": "infra_ticket"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ticket draft", response.json()["answer"])


if __name__ == "__main__":
    unittest.main()
