#!/usr/bin/env python3
"""Ingest Foundry onboarding docs into a clean text corpus under data/."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import html2text

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SOURCES_DIR = ROOT / "sources"
FOUNDRY_SITE = "https://foundry.mypaytm.com"

GDOCS_FAQ_ID = "1cFO96__cGuADEFvR_ahHcc0ILmYWvIrodjwMguihVbY"
GDOCS_FAQ_URL = f"https://docs.google.com/document/d/{GDOCS_FAQ_ID}/edit"
GDOCS_FAQ_EXPORT = f"https://docs.google.com/document/d/{GDOCS_FAQ_ID}/export?format=txt"


@dataclass(frozen=True)
class Source:
    slug: str
    url: str
    markdown_name: str
    local_relpath: str


SOURCES: tuple[Source, ...] = (
    Source(
        slug="getting-started",
        url=f"{FOUNDRY_SITE}/onboarding/",
        markdown_name="onboarding_index.md",
        local_relpath="doc-site/onboarding/index.md",
    ),
    Source(
        slug="getting-access",
        url=f"{FOUNDRY_SITE}/onboarding/getting-access",
        markdown_name="onboarding_getting-access.md",
        local_relpath="doc-site/onboarding/getting-access.md",
    ),
    Source(
        slug="set-up-cursor",
        url=f"{FOUNDRY_SITE}/onboarding/cursor",
        markdown_name="onboarding_cursor.md",
        local_relpath="doc-site/onboarding/cursor.md",
    ),
    Source(
        slug="updating-cli-plugin",
        url=f"{FOUNDRY_SITE}/onboarding/updating-cli-plugin",
        markdown_name="onboarding_updating-cli-plugin.md",
        local_relpath="doc-site/onboarding/updating-cli-plugin.md",
    ),
    Source(
        slug="faq",
        url=f"{FOUNDRY_SITE}/faq",
        markdown_name="faq.md",
        local_relpath="doc-site/faq.md",
    ),
)

GDOCS_SOURCE = Source(
    slug="faq-google-doc",
    url=GDOCS_FAQ_URL,
    markdown_name="",
    local_relpath="",
)


def _header(source_url: str) -> str:
    return f"Source: {source_url}\n\n"


def _html_converter() -> html2text.HTML2Text:
    conv = html2text.HTML2Text()
    conv.body_width = 0
    conv.ignore_links = False
    conv.ignore_images = True
    conv.ignore_emphasis = False
    conv.single_line_break = True
    conv.ul_item_mark = "-"
    return conv


def clean_markdown(text: str) -> str:
    """Strip VitePress/frontmatter boilerplate from raw markdown."""
    text = text.lstrip("\ufeff")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]

    text = re.sub(r"^#+\s*@page\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r":::+\s*\w[^\n]*\n(.*?)\n:::+\s*", r"\1\n", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def html_to_markdown(html: str) -> str:
    html = re.sub(
        r'<a class="header-anchor"[^>]*>.*?</a>',
        "",
        html,
        flags=re.DOTALL,
    )
    html = re.sub(r'\s*tabindex="-1"', "", html)
    html = html.replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&")
    md = _html_converter().handle(html)
    return clean_markdown(md)


def fetch_url(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ark-onboarding-bot/1.0 (+ingest.py)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_hash_map(site: str = FOUNDRY_SITE) -> dict[str, str]:
    html = fetch_url(f"{site}/onboarding/")
    match = re.search(r'window\.__VP_HASH_MAP__=JSON\.parse\("(.+?)"\)', html)
    if not match:
        raise RuntimeError("Could not parse VitePress hash map from foundry site")
    raw = match.group(1).encode("utf-8").decode("unicode_escape")
    return json.loads(raw)


def _unescape_js_template(raw: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "`":
                out.append("`")
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return "".join(out)


def extract_html_from_vitepress_js(js: str) -> str:
    candidates: list[str] = []
    for match in re.finditer(r"\w+\(`((?:\\.|[^`\\])*)`(?:,\d+)?\)", js, re.DOTALL):
        html = _unescape_js_template(match.group(1))
        if re.search(r"<h[1-6]|<p>|<ul>|<ol>|<table", html):
            candidates.append(html)
    if not candidates:
        raise RuntimeError("No VitePress content block found in JS bundle")
    return max(candidates, key=len)


def fetch_vitepress_markdown(source: Source, hash_map: dict[str, str]) -> str:
    content_hash = hash_map.get(source.markdown_name)
    if not content_hash:
        raise RuntimeError(f"No hash for {source.markdown_name} in VitePress map")
    asset_url = f"{FOUNDRY_SITE}/assets/{source.markdown_name}.{content_hash}.js"
    js = fetch_url(asset_url)
    html = extract_html_from_vitepress_js(js)
    return html_to_markdown(html)


def read_local_markdown(platform_root: Path, source: Source) -> str | None:
    path = platform_root / source.local_relpath
    if not path.is_file():
        return None
    return clean_markdown(path.read_text(encoding="utf-8"))


def ingest_foundry_page(
    source: Source,
    *,
    platform_root: Path | None,
    hash_map: dict[str, str],
) -> str:
    if platform_root is not None:
        local = read_local_markdown(platform_root, source)
        if local is not None:
            print(f"  local: {platform_root / source.local_relpath}")
            return local
    print(f"  web:   {source.markdown_name} via VitePress bundles")
    return fetch_vitepress_markdown(source, hash_map)


def fetch_gdocs_faq() -> str | None:
    try:
        body = fetch_url(GDOCS_FAQ_EXPORT)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            return None
        raise
    except urllib.error.URLError:
        return None
    if body.lstrip().startswith("<!DOCTYPE") or "Sign in to your Google Account" in body:
        return None
    text = body.replace("\r\n", "\n").strip()
    return text + "\n" if text else None


def ingest_gdocs_faq(*, manual_path: Path | None) -> str:
    exported = fetch_gdocs_faq()
    if exported:
        print("  web:   Google Docs export")
        return exported

    candidates = [
        manual_path,
        SOURCES_DIR / "faq-google-doc.txt",
        SOURCES_DIR / "faq-google-doc.md",
    ]
    for path in candidates:
        if path and path.is_file():
            print(f"  file:  {path}")
            return path.read_text(encoding="utf-8").strip() + "\n"

    msg = textwrap.dedent(
        f"""
        Google Docs FAQ could not be fetched automatically (auth required).

        Export the doc manually and save it to one of:
          - sources/faq-google-doc.txt
          - sources/faq-google-doc.md

        Or re-run with: --gdoc-file /path/to/export.txt

        Doc URL: {GDOCS_FAQ_URL}
        """
    ).strip()
    raise RuntimeError(msg)


def write_corpus(slug: str, source_url: str, body: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.md"
    path.write_text(_header(source_url) + body, encoding="utf-8")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DATA_DIR,
        help=f"Output directory (default: {DATA_DIR})",
    )
    parser.add_argument(
        "--foundry-platform",
        type=Path,
        default=None,
        help="Path to foundry-platform clone (or set FOUNDRY_PLATFORM_PATH)",
    )
    parser.add_argument(
        "--gdoc-file",
        type=Path,
        default=None,
        help="Manual Google Docs FAQ export (.txt or .md)",
    )
    parser.add_argument(
        "--site",
        default=FOUNDRY_SITE,
        help="Foundry docs site base URL",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip retrieval eval after ingest (local dev only)",
    )
    return parser.parse_args(argv)


def run_retrieval_eval() -> int:
    """Run gold-set retrieval eval; return 0 on pass, 1 on fail."""
    import subprocess

    cmd = [
        sys.executable,
        str(ROOT / "eval" / "run_eval.py"),
        "--quiet-retriever",
    ]
    print("\nRunning retrieval eval gate...")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("Eval gate FAILED — corpus not updated for production.", file=sys.stderr)
    else:
        print("Eval gate passed.")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    platform_root = args.foundry_platform
    if platform_root is None:
        env = os.environ.get("FOUNDRY_PLATFORM_PATH")
        if env:
            platform_root = Path(env)
    if platform_root is not None:
        platform_root = platform_root.expanduser().resolve()
        if not platform_root.is_dir():
            print(f"warning: FOUNDRY_PLATFORM_PATH not found: {platform_root}", file=sys.stderr)

    global FOUNDRY_SITE
    FOUNDRY_SITE = args.site.rstrip("/")

    print(f"Writing corpus to {args.out.resolve()}")
    hash_map = load_hash_map(FOUNDRY_SITE)
    written: list[Path] = []

    for source in SOURCES:
        print(f"[{source.slug}]")
        body = ingest_foundry_page(
            source,
            platform_root=platform_root if platform_root and platform_root.is_dir() else None,
            hash_map=hash_map,
        )
        path = write_corpus(source.slug, source.url, body, args.out)
        written.append(path)
        print(f"  -> {path} ({path.stat().st_size} bytes)")

    print(f"[{GDOCS_SOURCE.slug}]")
    try:
        gdoc_body = ingest_gdocs_faq(manual_path=args.gdoc_file)
        path = write_corpus(GDOCS_SOURCE.slug, GDOCS_SOURCE.url, gdoc_body, args.out)
        written.append(path)
        print(f"  -> {path} ({path.stat().st_size} bytes)")
    except RuntimeError as exc:
        print(f"  skipped: {exc}", file=sys.stderr)
        return 1

    print("\nSources ingested:")
    for path in written:
        print(f"  - {path.name}")

    if not args.skip_eval:
        return run_retrieval_eval()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
