"""Load the data/ corpus into citation-labelled chunks for retrieval."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# all-MiniLM-L6-v2 reads 256 tokens and silently drops the rest. At 2000 chars
# the tail of 45% of chunks never reached the encoder (a third of all corpus
# tokens). At 900, with headings budgeted into split pieces, it is 15.6% (68 of
# 437, measured 2026-08-28): code blocks and URLs tokenise far denser than
# prose, so a character budget can only approximate the token window.
MAX_CHARS = 900
OVERLAP_CHARS = 200


class Chunk(TypedDict):
    source: str
    text: str


def _split_fixed(text: str, max_chars: int, overlap: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap >= max_chars:
        raise ValueError("overlap must be less than max_chars")

    windows: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        windows.append(text[start:end])
        if end == n:
            break
        next_start = end - overlap
        if next_start <= start:
            break
        start = next_start
    return windows


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


def _heading_of(section: str) -> str | None:
    first = section.split("\n", 1)[0].strip()
    return first if first.startswith("#") else None


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
        # A split section keeps its heading on every piece. The pin markers key
        # on headings, so without this only the first piece of "## Start here"
        # would be pinned and the rest of the checklist would drop out of the
        # answer -- which is what happened when chunks shrank to fit the encoder.
        # The heading is budgeted inside the split so MAX_CHARS stays a bound.
        heading = _heading_of(section)
        prefix = f"{heading}\n\n" if heading else ""
        if len(section) <= MAX_CHARS:
            pieces = [section]
        else:
            pieces = _split_fixed(section, MAX_CHARS - len(prefix), OVERLAP_CHARS)
            pieces = [pieces[0]] + [prefix + p for p in pieces[1:]]
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
