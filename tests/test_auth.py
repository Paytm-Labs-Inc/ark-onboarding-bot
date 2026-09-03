"""Tests for the shared-token access gate."""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from src.auth import COOKIE_NAME
from src.web import app

TOKEN = "team-secret-token"


class AuthEnabledTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ARK_ACCESS_TOKEN"] = TOKEN
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)

    def test_index_redirects_to_login_when_unauthenticated(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_api_requires_auth(self) -> None:
        response = self.client.post("/api/ask", json={"question": "hi"})
        self.assertEqual(response.status_code, 401)

    def test_health_and_ready_stay_public(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)
        # /ready may be 200 or 503, but must not be gated (no redirect / 401).
        self.assertNotIn(self.client.get("/ready").status_code, (303, 401))

    def test_login_page_is_public(self) -> None:
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Access token", response.text)

    def test_login_rejects_bad_token(self) -> None:
        response = self.client.post("/login", json={"token": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_login_sets_cookie_then_allows_access(self) -> None:
        login = self.client.post("/login", json={"token": TOKEN})
        self.assertEqual(login.status_code, 200)
        self.assertIn(COOKIE_NAME, login.cookies)
        # The cookie is now in the client jar, so the gated page is reachable.
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)

    def test_bearer_token_authorizes_gated_route(self) -> None:
        response = self.client.get(
            "/reviews", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertEqual(response.status_code, 200)


class AuthDisabledTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)
        self.client = TestClient(app, follow_redirects=False)

    def test_open_when_no_token_configured(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/reviews").status_code, 200)


if __name__ == "__main__":
    unittest.main()


class SsoIdentityTests(unittest.TestCase):
    """The SSO header is only trustworthy where the ingress gate overwrites it."""

    HEADER = "X-Auth-Request-Email"

    def setUp(self) -> None:
        os.environ["ARK_ACCESS_TOKEN"] = TOKEN
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)
        os.environ.pop("SSO_IDENTITY_HEADER", None)

    def test_header_alone_is_not_enough_when_sso_is_not_configured(self) -> None:
        """The auth-bypass guard, and the reason this is opt-in.

        Without the ingress auth gate in front, nginx does not overwrite a
        client-supplied value. Honouring the header unconditionally would let
        anyone skip the token entirely by setting one header.
        """
        response = self.client.post(
            "/api/ask",
            json={"question": "hi"},
            headers={self.HEADER: "someone@paytm.com"},
        )
        self.assertEqual(response.status_code, 401)

    def test_header_authenticates_once_explicitly_configured(self) -> None:
        os.environ["SSO_IDENTITY_HEADER"] = self.HEADER
        response = self.client.post(
            "/api/ask",
            json={},  # invalid body: the route rejects it, but only after the gate
            headers={self.HEADER: "someone@paytm.com"},
        )
        # Anything other than the auth responses means the gate let it through.
        self.assertNotIn(response.status_code, (401, 303))

    def test_configured_but_empty_header_falls_back_to_the_token(self) -> None:
        os.environ["SSO_IDENTITY_HEADER"] = self.HEADER
        response = self.client.post(
            "/api/ask", json={"question": "hi"}, headers={self.HEADER: "   "}
        )
        self.assertEqual(response.status_code, 401)
