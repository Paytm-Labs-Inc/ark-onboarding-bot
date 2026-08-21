"""Tests for serving under a configurable subpath (BASE_PATH).

Covers both ingress behaviors:
- "strip"  : ingress removes the prefix, so the app receives unprefixed paths.
- "pass"   : ingress forwards the prefix, so the app receives prefixed paths and
             PrefixStripMiddleware normalizes them.
"""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from src.web import app

PREFIX = "/onboarding-bot"


class StripModeTests(unittest.TestCase):
    """Ingress strips the prefix -> app sees unprefixed paths."""

    def setUp(self) -> None:
        os.environ["BASE_PATH"] = PREFIX
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self) -> None:
        os.environ.pop("BASE_PATH", None)
        os.environ.pop("ARK_ACCESS_TOKEN", None)

    def test_index_injects_prefixed_base_and_relative_urls(self) -> None:
        text = self.client.get("/").text
        self.assertIn(f'<base href="{PREFIX}/">', text)
        self.assertIn('href="reviews"', text)
        self.assertNotIn('href="/reviews"', text)
        self.assertIn('fetch("api/ask/stream"', text)
        self.assertNotIn('fetch("/api/ask/stream"', text)

    def test_login_and_reviews_pages_have_prefixed_base(self) -> None:
        self.assertIn(f'<base href="{PREFIX}/">', self.client.get("/login").text)
        self.assertIn(f'<base href="{PREFIX}/">', self.client.get("/reviews").text)

    def test_unauthenticated_redirect_includes_prefix(self) -> None:
        os.environ["ARK_ACCESS_TOKEN"] = "team-secret-token"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{PREFIX}/login")

    def test_login_cookie_is_scoped_to_prefix(self) -> None:
        os.environ["ARK_ACCESS_TOKEN"] = "team-secret-token"
        response = self.client.post("/login", json={"token": "team-secret-token"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"Path={PREFIX}", response.headers.get("set-cookie", ""))


class PassThroughModeTests(unittest.TestCase):
    """Ingress passes the prefix through -> app sees prefixed paths."""

    def setUp(self) -> None:
        os.environ["BASE_PATH"] = PREFIX
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self) -> None:
        os.environ.pop("BASE_PATH", None)
        os.environ.pop("ARK_ACCESS_TOKEN", None)

    def test_prefixed_paths_route_correctly(self) -> None:
        self.assertEqual(self.client.get(f"{PREFIX}/").status_code, 200)
        self.assertEqual(self.client.get(f"{PREFIX}/login").status_code, 200)
        self.assertEqual(self.client.get(f"{PREFIX}/health").status_code, 200)

    def test_prefixed_index_still_serves_chat(self) -> None:
        text = self.client.get(f"{PREFIX}/").text
        self.assertIn("Ark Onboarding Bot", text)
        self.assertIn(f'<base href="{PREFIX}/">', text)

    def test_prefixed_api_is_gated_when_token_set(self) -> None:
        os.environ["ARK_ACCESS_TOKEN"] = "team-secret-token"
        # Unauthenticated API call at the passed-through path must still 401.
        response = self.client.post(f"{PREFIX}/api/ask", json={"question": "hi"})
        self.assertEqual(response.status_code, 401)


class RootPathUnsetTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("BASE_PATH", None)
        os.environ.pop("ROOT_PATH", None)
        self.client = TestClient(app, follow_redirects=False)

    def test_base_is_root_when_prefix_unset(self) -> None:
        self.assertIn('<base href="/">', self.client.get("/").text)


if __name__ == "__main__":
    unittest.main()
