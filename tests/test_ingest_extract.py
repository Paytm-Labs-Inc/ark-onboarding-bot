"""Tests for VitePress HTML extraction and roadmap markdown rendering."""

from __future__ import annotations

import unittest

from ingest.ingest import extract_html_from_vitepress_js, roadmap_js_to_markdown


class ExtractHtmlTests(unittest.TestCase):
    def test_prefers_backtick_html_block(self) -> None:
        js = 'x(`<p>short</p>`);y(`<h1>Title</h1><p>longer body here</p>`)'
        html = extract_html_from_vitepress_js(js)
        self.assertIn("<h1>Title</h1>", html)

    def test_reads_backticks_inside_html(self) -> None:
        js = 'o(`<h1>Getting Access</h1><p>run `ark login` then paste the key</p>`,3)'
        html = extract_html_from_vitepress_js(js)
        self.assertIn("Getting Access", html)
        self.assertIn("ark login", html)
        js = "n(h,e){return t('<h1 id=\"personal-credentials\">Personal Credentials</h1><p>Set JIRA_API_TOKEN.</p>')}"
        html = extract_html_from_vitepress_js(js)
        self.assertIn("Personal Credentials", html)
        self.assertIn("JIRA_API_TOKEN", html)

    def test_missing_html_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            extract_html_from_vitepress_js("const x = 1;")


class RoadmapMarkdownTests(unittest.TestCase):
    def test_renders_bets_matrix_and_sequence(self) -> None:
        theme_js = (
            '{__name:"Roadmap",setup(e){const t="https://docs.google.com/document/d/abc/edit",'
            'n=[{label:"Jira ticket",detail:"Work is tracked."}],'
            'a=[{tag:"0",title:"Make Ark Reliable",goal:"Stop the outages.",'
            'bullets:["Stop 503s."],success:"No outage."}],'
            's=[{title:"Managed compute",gaps:"Laptops.",capability:"EKS.",done:"Approved compute."}],'
            'c=[{label:"Compute",text:"Managed only."}],'
            'u=[{when:"Week of Aug 10, 2026",title:"Stable baseline",tasks:["Remove laptops."]}];'
            'return o("p",{class:"rm-hero-lede"},'
            '" Ark is the internal platform. ")}'
        )
        # Hero copy lives as HTML in the render function, not setup return.
        theme_js = theme_js.replace(
            'return o("p",{class:"rm-hero-lede"}," Ark is the internal platform. ")}',
            '<p class="rm-hero-lede"> Ark is the internal platform. </p>}',
        )
        md = roadmap_js_to_markdown(theme_js)
        self.assertIn("# Ark Roadmap", md)
        self.assertIn("Ark is the internal platform", md)
        self.assertIn("Live tracker: https://docs.google.com/document/d/abc/edit", md)
        self.assertIn("**Jira ticket:** Work is tracked.", md)
        self.assertIn("### 0. Make Ark Reliable", md)
        self.assertIn("**Goal:** Stop the outages.", md)
        self.assertIn("- Stop 503s.", md)
        self.assertIn("### Managed compute", md)
        self.assertIn("**Gap today:** Laptops.", md)
        self.assertIn("**Compute:** Managed only.", md)
        self.assertIn("### Week of Aug 10, 2026 — Stable baseline", md)


if __name__ == "__main__":
    unittest.main()
