"""Tests for the Foundry embed chat and overlay static assets."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.web import app


class OverlayRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("ARK_ACCESS_TOKEN", None)

    def test_embed_allows_framing(self) -> None:
        response = TestClient(app).get("/embed")
        self.assertEqual(response.status_code, 200)
        self.assertIn("New chat", response.text)
        self.assertNotIn("X-Frame-Options", response.headers)
        self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])

    def test_static_overlay_assets_served(self) -> None:
        client = TestClient(app)
        for path in ("/static/overlay.js", "/static/overlay.css"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)

    @patch.dict(os.environ, {"EMBED_FRAME_ANCESTORS": "https://foundry.mypaytm.com"})
    def test_embed_frame_ancestors_env(self) -> None:
        response = TestClient(app).get("/embed")
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("https://foundry.mypaytm.com", csp)


if __name__ == "__main__":
    unittest.main()
