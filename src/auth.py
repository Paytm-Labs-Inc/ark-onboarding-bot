"""Shared-token access gate for the web UI, with an SSO extension hook.

The gate is intentionally lightweight (a single team-wide token) so the deployed
UI is not open to anyone who can reach it. Real SSO can be layered in later via
`sso_stub_identity` without touching the routes.

Enforcement rule: auth is applied whenever `ARK_ACCESS_TOKEN` is set. When it is
unset the app runs open (handy for local dev and tests), but `src.web.main`
refuses to bind to a non-loopback interface in that state so a deployment can
never be accidentally exposed without a token.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Optional

COOKIE_NAME = "ark_session"
_BEARER_PREFIX = "bearer "

# Reachable without authentication: infra probes plus the login flow itself.
PUBLIC_PATHS = frozenset({"/health", "/ready", "/login", "/logout"})

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


def access_token() -> str:
    """The configured shared token (empty string means auth is disabled)."""
    return os.environ.get("ARK_ACCESS_TOKEN", "").strip()


def auth_enabled() -> bool:
    return bool(access_token())


def token_valid(candidate: Optional[str]) -> bool:
    """Constant-time comparison of a candidate token against the configured one."""
    expected = access_token()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate, expected)


def token_from_request(request: Any) -> Optional[str]:
    """Pull a token from `Authorization: Bearer ...` or the session cookie."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):].strip() or None
    cookie = request.cookies.get(COOKIE_NAME)
    return cookie or None


def request_authorized(request: Any) -> bool:
    """True when the request may proceed (auth disabled, SSO identity, or token)."""
    if not auth_enabled():
        return True
    if sso_stub_identity(request) is not None:
        return True
    return token_valid(token_from_request(request))


def sso_stub_identity(request: Any) -> Optional[str]:
    """Extension point for real SSO.

    A future OIDC/SSO integration would validate an upstream session or identity
    header here and return the authenticated user id. Returning None means "no
    SSO identity", so the shared-token path is used instead. Kept as a stub so
    the wiring exists without committing to a specific provider.
    """
    return None
