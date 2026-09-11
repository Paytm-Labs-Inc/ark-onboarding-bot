"""Intentional failure for Ask Ark Case 2 testing. Remove after the demo."""

from __future__ import annotations

import os
import unittest


@unittest.skipUnless(
    os.environ.get("CASE2_PROBE_FAIL") == "1",
    "Set CASE2_PROBE_FAIL=1 to activate the intentional Case 2 probe failure",
)
class Case2ProbeTests(unittest.TestCase):
    def test_intentional_probe_failure(self) -> None:
        self.fail("Intentional Case 2 probe failure — fix this for the demo")


if __name__ == "__main__":
    unittest.main()
