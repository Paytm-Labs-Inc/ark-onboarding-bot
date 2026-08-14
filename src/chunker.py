"""Load the data/ corpus into citation-labelled chunks for retrieval."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

MAX_CHARS = 2000
OVERLAP_CHARS = 200


class Chunk(TypedDict):
    source: str
    text: str


def _split_fixed(text: str, max_chars: int, overlap: int) -> list[str]:
    windows: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        windows.append(text[start:end])
        if end == n:
            break
        start = end - overlap
    return windows


SECTION_PREFIXES: dict[str, str] = {
    "### Register your own": (
        "How to use Ark after onboarding — create agents, apply a workspace, register a flow.\n\n"
    ),
    "### Use them over MCP": (
        "How to run Ark — discover flows, dispatch a session, and watch progress.\n\n"
    ),
    "### Worked example, end to end": (
        "End-to-end Ark usage: agent create, workspace apply, flow create, start session.\n\n"
    ),
    "## Onboarding path": (
        "Steps to onboard on Ark — full onboarding checklist and order.\n\n"
    ),
}


def _sections(body: str) -> list[str]:
    parts = re.split(r"(?m)^(?=## )", body)
    sections: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.search(r"(?m)^### ", part):
            subparts = re.split(r"(?m)^(?=### )", part)
            sections.extend(p.strip() for p in subparts if p.strip())
        else:
            sections.append(part)
    return sections


def _prefix_section(section: str) -> str:
    for heading, prefix in SECTION_PREFIXES.items():
        if section.startswith(heading):
            return prefix + section
    return section


def _chunk_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("Source:"):
        raise RuntimeError(f"{path}: missing 'Source:' header on first line")
    url = lines[0].split("Source:", 1)[1].strip()
    label = f"{path.stem} -- {url}"
    body = "\n".join(lines[1:]).strip()

    chunks: list[Chunk] = []
    for section in _sections(body):
        section = _prefix_section(section)
        pieces = [section] if len(section) <= MAX_CHARS else _split_fixed(
            section, MAX_CHARS, OVERLAP_CHARS
        )
        for piece in pieces:
            chunks.append({"source": label, "text": piece})
    return chunks


def load_chunks(data_dir: Path = DATA_DIR) -> list[Chunk]:
    """Return every {"source", "text"} chunk from the corpus under data_dir."""
    files = sorted(data_dir.glob("*.md"))
    if not files:
        raise RuntimeError(f"no .md corpus files found in {data_dir}")
    chunks: list[Chunk] = []
    for path in files:
        chunks.extend(_chunk_file(path))
    return chunks
