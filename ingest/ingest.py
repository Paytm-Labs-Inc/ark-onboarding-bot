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
        slug="secrets",
        url=f"{FOUNDRY_SITE}/onboarding/secrets",
        markdown_name="onboarding_secrets.md",
        local_relpath="doc-site/onboarding/secrets.md",
    ),
    Source(
        slug="slack-bot",
        url=f"{FOUNDRY_SITE}/onboarding/slack-bot",
        markdown_name="onboarding_slack-bot.md",
        local_relpath="doc-site/onboarding/slack-bot.md",
    ),
    Source(
        slug="team-infra",
        url=f"{FOUNDRY_SITE}/onboarding/team-infra",
        markdown_name="onboarding_team-infra.md",
        local_relpath="doc-site/onboarding/team-infra.md",
    ),
    Source(
        slug="compute",
        url=f"{FOUNDRY_SITE}/onboarding/compute",
        markdown_name="onboarding_compute.md",
        local_relpath="doc-site/onboarding/compute.md",
    ),
    Source(
        slug="k8s-compute",
        url=f"{FOUNDRY_SITE}/onboarding/k8s-compute",
        markdown_name="onboarding_k8s-compute.md",
        local_relpath="doc-site/onboarding/k8s-compute.md",
    ),
    Source(
        slug="registering-compute",
        url=f"{FOUNDRY_SITE}/onboarding/registering-compute",
        markdown_name="onboarding_registering-compute.md",
        local_relpath="doc-site/onboarding/registering-compute.md",
    ),
    Source(
        slug="admin",
        url=f"{FOUNDRY_SITE}/onboarding/admin",
        markdown_name="onboarding_admin.md",
        local_relpath="doc-site/onboarding/admin.md",
    ),
    Source(
        slug="first-run",
        url=f"{FOUNDRY_SITE}/onboarding/getting-started",
        markdown_name="onboarding_getting-started.md",
        local_relpath="doc-site/onboarding/getting-started.md",
    ),
    Source(
        slug="authoring-your-own",
        url=f"{FOUNDRY_SITE}/onboarding/authoring-your-own",
        markdown_name="onboarding_authoring-your-own.md",
        local_relpath="doc-site/onboarding/authoring-your-own.md",
    ),
    Source(
        slug="troubleshooting",
        url=f"{FOUNDRY_SITE}/onboarding/troubleshooting",
        markdown_name="onboarding_troubleshooting.md",
        local_relpath="doc-site/onboarding/troubleshooting.md",
    ),
    Source(
        slug="roadmap",
        url=f"{FOUNDRY_SITE}/roadmap/",
        markdown_name="roadmap_index.md",
        local_relpath="doc-site/roadmap/index.md",
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
            elif nxt == "'":
                out.append("'")
            elif nxt == '"':
                out.append('"')
            else:
                out.append(nxt)
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return "".join(out)


_HTML_HINT = re.compile(r"<h[1-6]|<p>|<ul>|<ol>|<table", re.IGNORECASE)
_BACKTICK_OPEN = re.compile(r"\w+\(`")
_BACKTICK_CLOSE = re.compile(r"`\s*(?:,\s*\d+)?\)")
_SINGLE_QUOTE_OPEN = re.compile(r"\w+\('")
_DOUBLE_QUOTE_OPEN = re.compile(r'\w+\("')


def _quoted_js_string(js: str, start: int, quote: str) -> str | None:
    """Return the raw contents of a JS string starting at start (after the opener)."""
    i = start
    n = len(js)
    while i < n:
        ch = js[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == quote:
            return js[start:i]
        i += 1
    return None


def extract_html_from_vitepress_js(js: str) -> str:
    candidates: list[str] = []
    for match in _BACKTICK_OPEN.finditer(js):
        closer = _BACKTICK_CLOSE.search(js, match.end())
        if closer is None:
            continue
        html = _unescape_js_template(js[match.end() : closer.start()])
        if _HTML_HINT.search(html):
            candidates.append(html)
    for opener, quote in ((_SINGLE_QUOTE_OPEN, "'"), (_DOUBLE_QUOTE_OPEN, '"')):
        for match in opener.finditer(js):
            raw = _quoted_js_string(js, match.end(), quote)
            if raw is None:
                continue
            html = _unescape_js_template(raw)
            if _HTML_HINT.search(html):
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


def _extract_js_bracket(src: str, open_idx: int) -> str:
    """Return the substring of a [...] or {...} literal starting at open_idx."""
    opener = src[open_idx]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_str: str | None = None
    escape = False
    for i in range(open_idx, len(src)):
        ch = src[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
    raise RuntimeError("Unbalanced JS bracket in roadmap bundle")


def _parse_js_string_array(raw: str) -> list[str]:
    return [
        _unescape_js_template(match.group(1))
        for match in re.finditer(r'"((?:\\.|[^"\\])*)"', raw)
    ]


def _parse_js_object(raw: str) -> dict[str, str | list[str]]:
    """Parse a minified JS object of string / string-array / boolean fields."""
    body = raw.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    out: dict[str, str | list[str]] = {}
    i = 0
    n = len(body)
    while i < n:
        key_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", body[i:])
        if not key_match:
            i += 1
            continue
        key = key_match.group(1)
        i += key_match.end()
        while i < n and body[i] in " \t\n":
            i += 1
        if i >= n:
            break
        if body[i] == "[":
            block = _extract_js_bracket(body, i)
            out[key] = _parse_js_string_array(block)
            i += len(block)
        elif body[i] in ('"', "'"):
            quote = body[i]
            m = re.match(rf"{quote}((?:\\.|[^{quote}\\])*){quote}", body[i:])
            if not m:
                break
            out[key] = _unescape_js_template(m.group(1))
            i += m.end()
        else:
            # boolean / number — skip to next comma at depth 0
            start = i
            while i < n and body[i] not in ",}":
                i += 1
            token = body[start:i].strip()
            if token:
                out[key] = token
        if i < n and body[i] == ",":
            i += 1
    return out


def _parse_js_object_array(raw: str) -> list[dict[str, str | list[str]]]:
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1].strip()
    if not inner:
        return []
    objects: list[dict[str, str | list[str]]] = []
    i = 0
    while i < len(inner):
        if inner[i] == "{":
            block = _extract_js_bracket(inner, i)
            objects.append(_parse_js_object(block))
            i += len(block)
            continue
        i += 1
    return objects


def _md_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def roadmap_js_to_markdown(theme_js: str) -> str:
    """Turn the Vue Roadmap component's data + hero copy into markdown."""
    start = theme_js.find('{__name:"Roadmap"')
    if start < 0:
        raise RuntimeError("Roadmap component not found in theme bundle")
    setup_at = theme_js.find("setup(e){", start)
    if setup_at < 0:
        raise RuntimeError("Roadmap setup() not found in theme bundle")
    setup = theme_js[setup_at:]

    gdoc_match = re.search(
        r'const t="(https://docs\.google\.com/document/d/[^"]+)"', setup
    )
    gdoc = gdoc_match.group(1) if gdoc_match else ""

    def _array_after(needle: str) -> list[dict[str, str | list[str]]]:
        idx = setup.find(needle)
        if idx < 0:
            return []
        bracket = setup.find("[", idx)
        return _parse_js_object_array(_extract_js_bracket(setup, bracket))

    pipeline = _array_after(",n=[") or _array_after("n=[")
    bets = _array_after(",a=[") or _array_after("a=[")
    matrix = _array_after(",s=[") or _array_after("s=[")
    decisions = _array_after(",c=[") or _array_after("c=[")
    sequence = _array_after(",u=[") or _array_after("u=[")

    lede_match = re.search(
        r'rm-hero-lede[^>]*>(.*?)</p>', setup, flags=re.DOTALL
    )
    lede = ""
    if lede_match:
        lede = re.sub(r"\s+", " ", lede_match.group(1)).strip()
        lede = (
            lede.replace("&#39;", "'")
            .replace("&quot;", '"')
            .replace("&amp;", "&")
        )

    parts: list[str] = ["# Ark Roadmap", ""]
    if lede:
        parts.extend([lede, ""])
    if gdoc:
        parts.extend([f"Live tracker: {gdoc}", ""])

    if pipeline:
        parts.append("## How work flows")
        parts.append("")
        for step in pipeline:
            label = str(step.get("label", "")).strip()
            detail = str(step.get("detail", "")).strip()
            if label and detail:
                parts.append(f"- **{label}:** {detail}")
            elif label:
                parts.append(f"- **{label}**")
        parts.append("")

    if bets:
        parts.append("## Product bets")
        parts.append("")
        for bet in bets:
            tag = str(bet.get("tag", "")).strip()
            title = str(bet.get("title", "")).strip()
            heading = f"{tag}. {title}".strip(". ")
            parts.append(f"### {heading}")
            parts.append("")
            goal = str(bet.get("goal", "")).strip()
            if goal:
                parts.append(f"**Goal:** {goal}")
                parts.append("")
            bullets = bet.get("bullets")
            if isinstance(bullets, list) and bullets:
                parts.append(_md_list([str(item) for item in bullets]))
                parts.append("")
            success = str(bet.get("success", "")).strip()
            if success:
                parts.append(f"**Done when:** {success}")
                parts.append("")

    if matrix:
        parts.append("## Eighteen areas, gap to done")
        parts.append("")
        for area in matrix:
            title = str(area.get("title", "")).strip()
            if not title:
                continue
            parts.append(f"### {title}")
            parts.append("")
            gaps = str(area.get("gaps", "")).strip()
            capability = str(area.get("capability", "")).strip()
            done = str(area.get("done", "")).strip()
            if gaps:
                parts.append(f"**Gap today:** {gaps}")
            if capability:
                parts.append(f"**What we will build:** {capability}")
            if done:
                parts.append(f"**Done when:** {done}")
            parts.append("")

    if decisions:
        parts.append("## Decisions (these are decided, not open)")
        parts.append("")
        for item in decisions:
            label = str(item.get("label", "")).strip()
            text = str(item.get("text", "")).strip()
            if label and text:
                parts.append(f"- **{label}:** {text}")
        parts.append("")

    if sequence:
        parts.append("## Sequence")
        parts.append("")
        for item in sequence:
            when = str(item.get("when", "")).strip()
            title = str(item.get("title", "")).strip()
            heading = " — ".join(p for p in (when, title) if p)
            parts.append(f"### {heading}")
            parts.append("")
            tasks = item.get("tasks")
            if isinstance(tasks, list) and tasks:
                parts.append(_md_list([str(task) for task in tasks]))
                parts.append("")

    text = "\n".join(parts).strip() + "\n"
    if len(text) < 400:
        raise RuntimeError("Roadmap markdown came out empty or too short")
    return text


def fetch_theme_js(site: str = FOUNDRY_SITE) -> str:
    html = fetch_url(f"{site}/roadmap/")
    app_match = re.search(r'src="(/assets/app\.[^"]+\.js)"', html)
    if not app_match:
        raise RuntimeError("Could not find VitePress app.js on /roadmap/")
    app_js = fetch_url(f"{site}{app_match.group(1)}")
    theme_match = re.search(r"chunks/theme\.[A-Za-z0-9_-]+\.js", app_js)
    if not theme_match:
        raise RuntimeError("Could not find theme chunk in app.js")
    return fetch_url(f"{site}/assets/{theme_match.group(0)}")


def fetch_roadmap_markdown() -> str:
    """The /roadmap/ page is a Vue component, not a markdown article."""
    return roadmap_js_to_markdown(fetch_theme_js())


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
    if source.slug == "roadmap":
        if platform_root is not None:
            local = read_local_markdown(platform_root, source)
            # The local file is often a Vue stub with no article body.
            if local is not None and len(local) > 400 and "Ark Roadmap" in local:
                print(f"  local: {platform_root / source.local_relpath}")
                return local
        print("  web:   roadmap Vue component via theme bundle")
        return fetch_roadmap_markdown()
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
        "--only-scored",
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
