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


def _sections(body: str) -> list[str]:
    parts = re.split(r"(?m)^(?=## )", body)
    return [p.strip() for p in parts if p.strip()]


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
