"""Query a CodeGraph index of the Foundry platform codebase."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from src.scout import ScoutReport

_MAX_EXCERPT = 6000
_DEFAULT_MAX_FILES = 8
_BUNDLED_DIR = "foundry-platform"


def repo_root() -> Path:
    """Root of the ark-onboarding-bot checkout (parent of src/)."""
    return Path(__file__).resolve().parent.parent


def _codegraph_bin() -> str:
    return os.environ.get("CODEGRAPH_BIN", "codegraph").strip() or "codegraph"


def bundled_foundry_path() -> Path:
    """Foundry platform checkout co-located with this app (same pod/image)."""
    return repo_root() / _BUNDLED_DIR


def project_path() -> Path:
    """Root of the repo CodeGraph should search.

    Resolution order:
    1. CODEGRAPH_PROJECT_PATH — explicit override
    2. ./foundry-platform — bundled clone in the same deployment (preferred)
    3. FOUNDRY_PLATFORM_PATH — local dev fallback
    4. ark-onboarding-bot root — last resort (onboarding bot source only)
    """
    for key in ("CODEGRAPH_PROJECT_PATH", "FOUNDRY_PLATFORM_PATH"):
        raw = os.environ.get(key, "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                return path

    bundled = bundled_foundry_path()
    if bundled.is_dir():
        return bundled

    return repo_root()


def is_foundry_codebase(path: Path | None = None) -> bool:
    """True when the index targets foundry-platform, not just this bot repo."""
    root = path or project_path()
    name = root.name.lower()
    if name == _BUNDLED_DIR or "foundry" in name:
        return True
    return bool(os.environ.get("FOUNDRY_PLATFORM_PATH", "").strip())


def index_ready(path: Path | None = None) -> bool:
    root = path or project_path()
    return (root / ".codegraph").is_dir()


def build_explore_query(report: ScoutReport) -> str:
    """Turn session failure signals into a CodeGraph explore query."""
    terms: list[str] = []
    err = report.error or ""

    terms.extend(re.findall(r"[\w./-]+\.(?:py|go|ts|tsx|js|jsx|rs|yaml|yml)", err)[:4])

    lower = err.lower()
    if "arkd" in lower or "ark-darwin" in lower or "/$bunfs/" in lower:
        terms.extend(["arkd", "runtime", "executor"])
    if "unknown method" in lower or "jsonrpc" in lower:
        terms.extend(["session", "rpc", "lifecycle"])
    if "workspace" in lower and "prepare" in lower:
        terms.extend(["workspace", "prepare"])
    if "codegraph" in lower:
        terms.append("codegraph")

    stop = {
        "error",
        "failed",
        "cannot",
        "message",
        "resolvemessage",
        "process",
        "stage",
        "session",
        "unknown",
    }
    for token in re.split(r"\W+", err):
        t = token.strip()
        if len(t) >= 5 and t.lower() not in stop:
            terms.append(t)

    if report.stage:
        terms.append(report.stage)
    if report.flow_name:
        terms.append(report.flow_name)

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)

    return " ".join(deduped[:12]) or "session workspace flow compute"


def explore_report(report: ScoutReport) -> tuple[str, list[dict[str, str]]]:
    """Run codegraph explore; return excerpt text and structured chunks."""
    root = project_path()
    if not index_ready(root):
        return "", []

    if shutil.which(_codegraph_bin()) is None:
        return "", []

    query = build_explore_query(report)
    cmd = [
        _codegraph_bin(),
        "explore",
        "-p",
        str(root),
        "--max-files",
        str(int(os.environ.get("CODEGRAPH_MAX_FILES", _DEFAULT_MAX_FILES))),
        *query.split(),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=float(os.environ.get("CODEGRAPH_TIMEOUT_SECONDS", "45")),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", []

    output = (proc.stdout or "").strip()
    if proc.returncode != 0 or not output:
        return "", []
    if "isn't available" in output.lower() or "not initialized" in output.lower():
        return "", []

    excerpt = output[:_MAX_EXCERPT]
    chunks: list[dict[str, str]] = [
        {
            "source": f"codegraph:{root.name}",
            "text": excerpt,
        }
    ]
    return excerpt, chunks
