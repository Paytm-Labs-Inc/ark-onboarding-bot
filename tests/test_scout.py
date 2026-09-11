"""Tests for Scout session gathering."""

from __future__ import annotations

import json
import unittest

from src.ark_client import ArkClient
from src.scout import ScoutReport, gather_scout_report, session_succeeded


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _rpc_opener(responses: dict[str, object]):
    def opener(request, timeout=60):
        body = json.loads(request.data.decode("utf-8"))
        method = body["method"]
        op = body.get("params", {}).get("op")
        key = f"{method}:{op}" if op else method
        if key not in responses and method in responses:
            key = method
        result = responses.get(key, responses.get(method, {}))
        return _FakeResponse({"jsonrpc": "2.0", "id": 1, "result": result})

    return opener


class ScoutTests(unittest.TestCase):
    def test_gather_scout_report(self) -> None:
        show = {
            "status": "failed",
            "stage": "verify",
            "summary": "fix auth bug",
            "flow": "ark-feature",
            "config": {"workspace": "my-workspace"},
            "compute_name": "my-compute",
            "error": "auth: MISSING",
            "stage_results": {"verify": {"ok": False, "message": "tests failed"}},
        }
        client = ArkClient(
            api_key="test-key",
            opener=_rpc_opener(
                {
                    "session/read:show": {"session": show},
                    "session/read:events": [{"type": "session_failed"}],
                    "session/read:output": "Error: auth missing\n",
                    "session/read:transcript": "tool call failed",
                    "session/read:action_results": [],
                    "worktree/read:diff": {"stat": " 1 file changed", "diff": ""},
                    "worktree/read:stage_diffs": {"stages": []},
                    "session/artifacts/read:list": {"artifacts": [{"name": "triage.md"}]},
                    "costs/read:session": {"totalUsd": 1.25},
                    "flow/read:show": {"name": "ark-feature"},
                    "workspace/read:show": {"name": "my-workspace"},
                    "workspace/read:runtime_list": {"runtimes": []},
                }
            ),
        )
        report = gather_scout_report("s-abc1234567", client=client)
        self.assertTrue(report.found)
        self.assertEqual(report.session_id, "s-abc1234567")
        self.assertEqual(report.error, "auth: MISSING")
        self.assertEqual(report.flow_name, "ark-feature")
        self.assertEqual(report.workspace_name, "my-workspace")
        self.assertEqual(report.compute_name, "my-compute")
        self.assertEqual(report.cost_usd, 1.25)
        self.assertIn("triage.md", report.artifacts)
        self.assertEqual(len(report.raw_events), 1)
        self.assertEqual(report.raw_events[0]["type"], "session_failed")
        self.assertEqual(report.raw_action_results, [])

    def test_session_succeeded_when_completed_without_error(self) -> None:
        report = ScoutReport(
            session_id="s-fbtj7o5j90",
            found=True,
            status="completed",
            stage="smoke",
            gather_errors=["worktree_diff: Unknown method: worktree"],
        )
        self.assertTrue(session_succeeded(report))

    def test_session_not_succeeded_when_failed(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            status="failed",
            stage="pr",
            error="git push failed",
        )
        self.assertFalse(session_succeeded(report))

    def test_session_not_succeeded_when_completed_but_stage_failed(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            status="completed",
            stage="verify",
            raw_show={"stage_results": {"verify": {"ok": False, "message": "tests failed"}}},
        )
        self.assertFalse(session_succeeded(report))

    def test_missing_session(self) -> None:
        client = ArkClient(
            api_key="test-key",
            opener=_rpc_opener({"session/read:show": None}),
        )
        report = gather_scout_report("s-missing123", client=client)
        self.assertFalse(report.found)


if __name__ == "__main__":
    unittest.main()
