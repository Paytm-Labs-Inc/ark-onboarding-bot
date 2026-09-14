"""Read ArgoCD application sync/health for Case 1 deployment status."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ArgoAppStatus:
    name: str
    sync_status: str = "Unknown"
    health: str = "Unknown"
    revision: str = ""
    message: str = ""

    def summary(self) -> str:
        parts = [f"{self.name}: Sync={self.sync_status}, Health={self.health}"]
        if self.revision:
            parts.append(f"revision `{self.revision[:12]}`")
        if self.message:
            parts.append(self.message)
        return ", ".join(parts)


def _argocd_config() -> tuple[str, str, list[str]]:
    url = os.environ.get("ARGOCD_URL", "").strip().rstrip("/")
    token = os.environ.get("ARGOCD_TOKEN", "").strip()
    apps_raw = os.environ.get("ARGOCD_APPS", "").strip()
    apps = [a.strip() for a in apps_raw.split(",") if a.strip()]
    return url, token, apps


def get_app_status(
    app_name: str,
    *,
    base_url: str | None = None,
    token: str | None = None,
    opener: Callable[..., Any] | None = None,
    timeout: float = 30.0,
) -> ArgoAppStatus:
    url_base, tok, _ = _argocd_config()
    base_url = (base_url or url_base).rstrip("/")
    token = token if token is not None else tok
    status = ArgoAppStatus(name=app_name)

    if not base_url or not token:
        status.message = "ArgoCD not configured (set ARGOCD_URL and ARGOCD_TOKEN)."
        return status

    endpoint = f"{base_url}/api/v1/applications/{app_name}"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status.message = f"HTTP {exc.code}"
        return status
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        status.message = str(exc)
        return status

    sync = data.get("status", {}).get("sync", {})
    health = data.get("status", {}).get("health", {})
    status.sync_status = str(sync.get("status") or "Unknown")
    status.health = str(health.get("status") or "Unknown")
    status.revision = str(sync.get("revision") or data.get("status", {}).get("operationState", {}).get("syncResult", {}).get("revision") or "")
    return status


def format_deployment_status(fix_ref: str | None = None) -> str:
    url, token, apps = _argocd_config()
    if not url or not token or not apps:
        return ""
    lines = ["Deployment status (ArgoCD):"]
    for app in apps[:5]:
        st = get_app_status(app)
        line = st.summary()
        if fix_ref and fix_ref[:12] in st.revision:
            line += " — fix revision is deployed"
        lines.append(f"- {line}")
    return "\n".join(lines)
