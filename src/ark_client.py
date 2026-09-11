"""HTTP client for the Ark control-plane JSON-RPC API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

DEFAULT_ARK_API_URL = (
    "https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc"
)


class ArkError(RuntimeError):
    """Ark RPC call failed."""


class ArkClient:
    """Thin wrapper around Ark's JSON-RPC surface."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        opener: Callable[..., Any] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ARK_API_URL") or DEFAULT_ARK_API_URL).rstrip("/")
        self.api_key = (api_key if api_key is not None else os.environ.get("ARK_API_KEY", "")).strip()
        self.opener = opener
        self.timeout = timeout
        self._request_id = 0

    def configured(self) -> bool:
        return bool(self.api_key)

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api_key:
            raise ArkError(
                "ARK_API_KEY is not set. Add your Ark user API key to enable session debug."
            )
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        send = self.opener or urllib.request.urlopen
        try:
            with send(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ArkError(f"Ark HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ArkError(f"Could not reach Ark at {self.base_url}: {exc.reason}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArkError(f"Ark returned non-JSON: {raw[:200]}") from exc

        if "error" in parsed:
            err = parsed["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise ArkError(f"Ark RPC error: {message}")
        return parsed.get("result")

    def session_read(self, op: str, **kwargs: Any) -> Any:
        # JSON-RPC uses category/action (e.g. auth/whoami), not MCP tool names.
        return self.rpc("session/read", {"op": op, **kwargs})

    def session_lifecycle(self, op: str, **kwargs: Any) -> Any:
        # HTTP JSON-RPC splits lifecycle verbs (session/start, session/stop, …).
        # MCP exposes them as one session_lifecycle tool with an op param.
        # session/start reads compute_name; MCP callers pass compute.
        http_kwargs = dict(kwargs)
        if op == "start" and "compute" in http_kwargs and "compute_name" not in http_kwargs:
            http_kwargs["compute_name"] = http_kwargs["compute"]
        param_variants: list[dict[str, Any]] = [{"op": op, **http_kwargs}]
        legacy_params = {"op": op, **kwargs}
        if legacy_params != param_variants[0]:
            param_variants.append(legacy_params)
        return self._rpc_first_with_params(
            (f"session/{op}", "session/lifecycle", "session_lifecycle"),
            *param_variants,
        )

    def flow(self, op: str, **kwargs: Any) -> Any:
        return self.rpc("flow/read", {"op": op, **kwargs})

    def workspace(self, op: str, **kwargs: Any) -> Any:
        return self.rpc("workspace/read", {"op": op, **kwargs})

    def _rpc_first(self, methods: tuple[str, ...], op: str, **kwargs: Any) -> Any:
        """Try several JSON-RPC method names (HTTP vs MCP naming drift)."""
        return self._rpc_first_with_params(
            methods,
            {"op": op, **kwargs},
        )

    def _rpc_first_with_params(
        self,
        methods: tuple[str, ...],
        *param_variants: dict[str, Any],
    ) -> Any:
        """Try method names, then param shapes, until one succeeds."""
        last: ArkError | None = None
        for index, method in enumerate(methods):
            unknown_method = False
            method_last: ArkError | None = None
            for params in param_variants:
                try:
                    return self.rpc(method, params)
                except ArkError as exc:
                    method_last = exc
                    if "Unknown method" in str(exc):
                        unknown_method = True
                        break
                    continue
            if method_last is not None:
                last = method_last
            # A real failure on the canonical HTTP method must not fall through
            # to legacy names — that hides errors like "compute not available".
            if index == 0 and method_last is not None and not unknown_method:
                raise method_last
        raise last or ArkError("No RPC method accepted")

    def worktree(self, op: str, **kwargs: Any) -> Any:
        return self._rpc_first(("worktree/read", "worktree"), op, **kwargs)

    def costs(self, op: str, **kwargs: Any) -> Any:
        return self.rpc("costs/read", {"op": op, **kwargs})

    def session_artifacts(self, op: str, **kwargs: Any) -> Any:
        return self._rpc_first(
            ("session/artifacts/read", "session/artifacts", "session_artifacts"),
            op,
            **kwargs,
        )


def default_client() -> ArkClient:
    return ArkClient()
