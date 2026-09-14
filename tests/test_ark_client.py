"""Tests for Ark HTTP JSON-RPC client method naming."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.ark_client import ArkClient, ArkError


class ArkClientLifecycleTests(unittest.TestCase):
    def test_session_lifecycle_start_maps_compute_to_compute_name(self) -> None:
        client = ArkClient(api_key="test-key")
        calls: list[tuple[str, dict]] = []

        def fake_rpc(method: str, params: dict | None = None) -> dict:
            calls.append((method, params or {}))
            if method == "session/start" and params and params.get("compute_name") == "aneetta-mac":
                return {"sessionId": "s-abc"}
            raise ArkError(f"boom: {method}")

        client.rpc = fake_rpc  # type: ignore[method-assign]
        result = client.session_lifecycle(
            "start",
            flow="default",
            repo="https://example.com/repo.git",
            compute="aneetta-mac",
            summary="test",
        )
        self.assertEqual(result, {"sessionId": "s-abc"})
        self.assertEqual(calls[0][0], "session/start")
        self.assertEqual(calls[0][1]["compute_name"], "aneetta-mac")

    def test_rpc_first_does_not_mask_real_session_start_errors(self) -> None:
        client = ArkClient(api_key="test-key")
        calls: list[str] = []

        def fake_rpc(method: str, params: dict | None = None) -> dict:
            calls.append(method)
            if method == "session/start":
                raise ArkError("Ark RPC error: compute 'bad' is not available to dispatch on")
            raise ArkError(f"Unknown method: {method}")

        client.rpc = fake_rpc  # type: ignore[method-assign]
        with self.assertRaises(ArkError) as ctx:
            client.session_lifecycle("start", compute="bad", summary="hello")
        self.assertIn("not available to dispatch on", str(ctx.exception))
        self.assertEqual(calls, ["session/start", "session/start"])

    def test_rpc_first_tries_session_start_before_legacy_names(self) -> None:
        client = ArkClient(api_key="test-key")
        calls: list[tuple[str, dict]] = []

        def fake_rpc(method: str, params: dict | None = None) -> dict:
            calls.append((method, params or {}))
            if method == "session/start":
                return {"sessionId": "s-ok"}
            raise ArkError(f"Unknown method: {method}")

        client.rpc = fake_rpc  # type: ignore[method-assign]
        result = client.session_lifecycle("start", summary="hello")
        self.assertEqual(result, {"sessionId": "s-ok"})
        self.assertEqual(calls[0][0], "session/start")
        self.assertEqual(calls[0][1]["op"], "start")
        self.assertEqual(calls[0][1]["summary"], "hello")


if __name__ == "__main__":
    unittest.main()
