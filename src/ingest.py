"""Refresh the onboarding corpus from upstream docs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INGEST_SCRIPT = ROOT / "ingest" / "ingest.py"


def _load_ingest_module():
    spec = importlib.util.spec_from_file_location("ingest_pipeline", INGEST_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load ingest script: {INGEST_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    """Re-pull Foundry onboarding docs into data/ (alias: --refresh)."""
    args = list(argv if argv is not None else sys.argv[1:])
    args = [arg for arg in args if arg != "--refresh"]
    ingest = _load_ingest_module()
    return int(ingest.main(args))


if __name__ == "__main__":
    raise SystemExit(main())
