"""Parse corpus citation labels into display metadata."""

from __future__ import annotations


def parse_citation(source: str) -> dict[str, str]:
    """Split 'slug -- https://...' labels from the chunker."""
    label = source.strip()
    if " -- " in label:
        slug, url = label.split(" -- ", 1)
        return {"slug": slug.strip(), "url": url.strip(), "label": label}
    return {"slug": label, "url": "", "label": label}
