"""Tests for src.ingest wrapper."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src import ingest as ingest_module


class IngestModuleTests(unittest.TestCase):
    @patch.object(ingest_module, "_load_ingest_module")
    def test_refresh_strips_flag_and_delegates(self, mock_load: MagicMock) -> None:
        pipeline = MagicMock()
        pipeline.main.return_value = 0
        mock_load.return_value = pipeline

        code = ingest_module.main(["--refresh", "--out", "/tmp/data"])

        self.assertEqual(code, 0)
        pipeline.main.assert_called_once_with(["--out", "/tmp/data"])


if __name__ == "__main__":
    unittest.main()
