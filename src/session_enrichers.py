"""Enrich Scout reports with RAG, codebase, and changelog context."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.codegraph_client import explore_report, is_foundry_codebase, project_path
from src.retrieve import retrieve_scored
from src.scout import ScoutReport


CHANGELOG_UNAVAILABLE = (
    "Changelog evidence unavailable in this deployment (no git binary or checkout). "
    "Case 1 'already fixed' matching needs a Foundry repo with git."
)


@dataclass
class EnrichmentBundle:
    rag_chunks: list[dict[str, Any]] = field(default_factory=list)
    code_chunks: list[dict[str, Any]] = field(default_factory=list)
    changelog_hits: list[dict[str, str]] = field(default_factory=list)
    changelog_note: str = ""
    worktree_summary: str = ""
    codegraph_summary: str = ""

    def rag_text(self) -> str:
        return "\n\n".join(
            f"[{c.get('source', 'doc')}] {c.get('text', '')[:600]}"
            for c in self.rag_chunks[:6]
        )

    def code_text(self) -> str:
        parts: list[str] = []
        if self.codegraph_summary:
            parts.append(f"[codegraph] {self.codegraph_summary[:1200]}")
        parts.extend(
            f"[{c.get('source', 'code')}] {c.get('text', '')[:600]}"
            for c in self.code_chunks[:6]
        )
        return "\n\n".join(parts)

    def changelog_text(self) -> str:
        if not self.changelog_hits:
            return ""
        return "\n".join(
            f"- {h.get('ref', '?')}: {h.get('subject', '')} ({h.get('date', '')})"
            for h in self.changelog_hits[:8]
        )


def git_repo_ready(root: Path) -> bool:
    """True when git can read commit history from *root*."""
    if not shutil.which("git") or not root.is_dir():
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _grep_codebase(query: str, root: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    """Best-effort git grep when CodeGraph is unavailable."""
    tokens = [t for t in re.split(r"\W+", query) if len(t) > 3][:5]
    if not tokens:
        return []
    pattern = tokens[0]
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "grep", "-n", "-i", pattern, "--", "*.py", "*.go", "*.ts", "*.tsx"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    hits: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines()[:limit * 3]:
        parts = line.split(":", 2)
        if len(parts) >= 3:
            hits.append(
                {
                    "source": f"{parts[0]}:{parts[1]}",
                    "text": parts[2][:400],
                }
            )
        if len(hits) >= limit:
            break
    return hits


def _recent_commits(root: Path, *, paths: list[str] | None = None, limit: int = 10) -> list[dict[str, str]]:
    cmd = [
        "git",
        "-C",
        str(root),
        "log",
        f"-{limit}",
        "--pretty=format:%H|%s|%ci",
    ]
    if paths:
        cmd.extend(["--"] + paths[:5])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    hits: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            hits.append({"ref": parts[0][:12], "subject": parts[1], "date": parts[2]})
    return hits


def _files_from_worktree(report: ScoutReport) -> list[str]:
    text = report.worktree_stat + report.worktree_diff
    return re.findall(r"^\s*(?:[\w\-./]+\.(?:py|go|ts|tsx|yaml|yml))\b", text, re.MULTILINE)[:10]


def enrich_report(report: ScoutReport, *, top_k: int = 6) -> EnrichmentBundle:
    bundle = EnrichmentBundle()
    query = report.retrieval_query()

    scored = retrieve_scored(query, k=top_k)
    bundle.rag_chunks = list(scored.chunks)

    cg_excerpt, cg_chunks = explore_report(report)
    if cg_excerpt:
        bundle.codegraph_summary = cg_excerpt
        bundle.code_chunks.extend(cg_chunks)

    root = project_path()
    if not bundle.code_chunks:
        bundle.code_chunks = _grep_codebase(query, root)
    paths = _files_from_worktree(report)
    if git_repo_ready(root) and is_foundry_codebase(root):
        bundle.changelog_hits = _recent_commits(root, paths=paths or None)
    else:
        bundle.changelog_note = CHANGELOG_UNAVAILABLE

    if report.stage_diffs:
        bundle.worktree_summary = report.stage_diffs[:2000]
    elif report.worktree_stat:
        bundle.worktree_summary = report.worktree_stat[:2000]

    return bundle
