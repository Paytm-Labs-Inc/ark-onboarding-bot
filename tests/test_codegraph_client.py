"""Tests for CodeGraph enrichment client."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.codegraph_client import (
    build_explore_query,
    bundled_foundry_path,
    explore_report,
    index_ready,
    is_foundry_codebase,
    project_path,
    repo_root,
)
from src.scout import ScoutReport


class CodegraphClientTests(unittest.TestCase):
    def test_build_explore_query_from_error(self) -> None:
        report = ScoutReport(
            session_id="s-abc1234567",
            found=True,
            error="ResolveMessage: Cannot find module './986.js' from '/$bunfs/root/ark-darwin-arm64'",
            stage="triage",
            flow_name="ark-feature",
        )
        query = build_explore_query(report)
        self.assertIn("986.js", query)
        self.assertIn("arkd", query)
        self.assertIn("triage", query)

    @patch("src.codegraph_client.shutil.which", return_value="/usr/bin/codegraph")
    @patch("src.codegraph_client.subprocess.run")
    @patch("src.codegraph_client.project_path")
    @patch("src.codegraph_client.index_ready", return_value=True)
    def test_explore_report_returns_excerpt(
        self,
        _ready: MagicMock,
        mock_path: MagicMock,
        mock_run: MagicMock,
        _which: MagicMock,
    ) -> None:
        mock_path.return_value = Path("/tmp/foundry-platform")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="=== AuthService.login ===\nfunction login() {}",
            stderr="",
        )
        report = ScoutReport(session_id="s-x", found=True, error="login failed")
        excerpt, chunks = explore_report(report)
        self.assertIn("AuthService", excerpt)
        self.assertEqual(len(chunks), 1)
        mock_run.assert_called_once()

    def test_project_path_prefers_bundled_foundry(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "foundry-platform"
            bundled.mkdir()
            with patch("src.codegraph_client.repo_root", return_value=root):
                with patch.dict("os.environ", {}, clear=True):
                    self.assertEqual(project_path(), bundled)
                    self.assertTrue(is_foundry_codebase())

    def test_project_path_falls_back_to_repo_root(self) -> None:
        with patch("src.codegraph_client.bundled_foundry_path") as mock_bundled:
            mock_bundled.return_value = repo_root() / "missing-foundry"
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(project_path(), repo_root())
                self.assertFalse(is_foundry_codebase())

    @patch("src.codegraph_client.index_ready", return_value=False)
    def test_explore_skips_without_index(self, _ready: MagicMock) -> None:
        report = ScoutReport(session_id="s-x", found=True, error="oops")
        excerpt, chunks = explore_report(report)
        self.assertEqual(excerpt, "")
        self.assertEqual(chunks, [])

    def test_index_ready_checks_dot_codegraph(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(index_ready(root))
            (root / ".codegraph").mkdir()
            self.assertTrue(index_ready(root))


if __name__ == "__main__":
    unittest.main()
