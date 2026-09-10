"""Shared-token access gate for the web UI, with an SSO extension hook.

The gate is intentionally lightweight (a single team-wide token) so the deployed
UI is not open to anyone who can reach it. Real SSO can be layered in later via
`sso_identity` without touching the routes.

Enforcement rule: auth is applied whenever `ARK_ACCESS_TOKEN` is set. When it is
unset the app runs open (handy for local dev and tests), but `src.web.main`
refuses to bind to a non-loopback interface in that state so a deployment can
never be accidentally exposed without a token.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from typing import Any, Optional

COOKIE_NAME = "ark_session"

# Opaque per-browser id, used ONLY to scope stored chat threads. Deliberately a
# separate cookie from the session token above: that one authenticates, this one
# merely says which pile of chats to show. Conflating them would make deleting a
# chat history a sign-out, and clearing a cookie an auth bypass.
BROWSER_ID_COOKIE = "ark_uid"
_BROWSER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
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
    if sso_identity(request) is not None:
        return True
    return token_valid(token_from_request(request))


def sso_identity_header() -> str:
    """Name of the header carrying the SSO-verified user, or "" when unconfigured."""
    return os.environ.get("SSO_IDENTITY_HEADER", "").strip()


def sso_identity(request: Any) -> Optional[str]:
    """The user id an upstream SSO proxy vouched for, or None.

    oauth2-proxy runs as nginx external auth: it authenticates the browser and
    nginx copies the identity onto the upstream request, overwriting whatever
    the client sent. We read that header rather than speaking OIDC ourselves.

    Trusted ONLY when `SSO_IDENTITY_HEADER` names a header explicitly, because
    the header is only trustworthy while two things hold: the ingress auth gate
    is on (so nginx overwrites a client-supplied value) and nothing but nginx
    can reach the pod (the Service is ClusterIP-only, the same reasoning behind
    accepting FORWARDED_ALLOW_IPS="*").

    Reading it unconditionally would therefore be an auth BYPASS, not an
    upgrade: absent the gate, anyone could send the header and skip the token.
    Unset means "no SSO", so this fails closed by default and turns on only
    where the chart also renders the gate.
    """
    header = sso_identity_header()
    if not header:
        return None
    return (request.headers.get(header, "") or "").strip() or None


def new_browser_id() -> str:
    """A fresh opaque browser id. Not a secret and not a credential."""
    return secrets.token_urlsafe(16)


def browser_identity(request: Any) -> Optional[str]:
    """The id this browser was given, or None if it has none or a malformed one.

    Validated against a strict charset rather than trusted: it is attacker-
    controlled (a cookie), and it will be used to build storage keys. A value
    carrying a path separator or a wildcard could otherwise reach across
    another user's keyspace in whichever store ends up behind this.
    """
    raw = (request.cookies.get(BROWSER_ID_COOKIE, "") or "").strip()
    return raw if _BROWSER_ID_RE.match(raw) else None


def current_identity(request: Any) -> Optional[str]:
    """Who this request's stored data belongs to, or None if nobody yet.

    SSO first, browser cookie second. The order is the whole point: once the
    ingress gate is on, a real person's identity must win over the browser they
    happen to be sitting at, so history follows the human across devices rather
    than staying with the machine.

    This is IDENTITY, never AUTHORISATION. `authorized()` is unchanged and does
    not consult it. A browser id proves nothing -- anyone can send one -- so a
    request carrying only a cookie is still gated by the token exactly as
    before. Treating this as auth would turn "clear your cookies" into "become
    a new user with a free pass".
    """
    return sso_identity(request) or browser_identity(request)
