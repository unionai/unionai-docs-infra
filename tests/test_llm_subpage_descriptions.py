#!/usr/bin/env python3
"""Guard the child description in a `## Subpages` listing (DOC-1508).

Hugo writes the child's front-matter description into the listing
(`layouts/_default/list.md`):

    - [Title](child.md) - what the child page is about

`enhance_subpage_listings()` then rewrites that block to add each child's H2/H3
headings. It used to re-parse the block with a regex that captured only the
title and the URL, so the rewrite dropped every description -- 4 of 690 entries
in the union build, 2 of 571 in flyte, all of them, silently.

The listing is the table of contents an agent reads before deciding which page
to fetch. A title alone says less than a title and a sentence, so the
description is worth more there than anywhere else on the page.

These tests pin the four cases that a naive regex gets wrong, plus the two that
must not change: a child with no description, and the headings.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import LLMDocBuilder  # noqa: E402
from page_paths import root_page  # noqa: E402


class Tree:
    """A source `content/` tree and its built `dist/` twin, kept in step.

    Both are needed: `enhance_subpage_listings()` resolves each child against
    the SOURCE tree to find its headings, so a fixture that builds only the
    output tree resolves nothing.
    """

    def __init__(self, tmp_path: Path):
        self.base = tmp_path
        self.content = tmp_path / "content"
        self.out = tmp_path / "dist" / "docs" / "v2" / "union"

    def section(self, rel: str, title: str, children=()):
        """A Hugo section. `children` are `(name, description)` pairs, written
        exactly as `layouts/_default/list.md` writes them."""
        src = self.content / rel if rel else self.content
        src.mkdir(parents=True, exist_ok=True)
        (src / "_index.md").write_text(f"---\ntitle: {title}\n---\n", encoding="utf-8")

        page = f"# {title}\n"
        if children:
            page += "\n## Subpages\n\n"
            for name, description in children:
                url = name if name.startswith("http") else f"{name}.md"
                tail = f" - {description}" if description else ""
                page += f"- [{name}]({url}){tail}\n"
        self._write_out(rel, page)

    def leaf(self, rel: str, title: str, headings=()):
        src = self.content / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.with_suffix(".md").write_text(f"---\ntitle: {title}\n---\n", encoding="utf-8")
        page = f"# {title}\n"
        for heading in headings:
            page += f"\n## {heading}\n"
        self._write_out(rel, page)

    def _write_out(self, rel: str, text: str):
        f = (self.out / (rel + ".md")) if rel else root_page(self.out)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def enhance(self) -> LLMDocBuilder:
        b = LLMDocBuilder(self.base, quiet=True)
        b.version = "v2"
        b.variant_root = self.out
        b.build_lookup_tables(root_page(self.out), self.out, [])
        b.enhance_subpage_listings("union")
        return b

    def listing(self, rel: str) -> str:
        return (self.out / (rel + ".md")).read_text(encoding="utf-8")


BRACKETS = "Options - [the API](api.md), `x[0]`, and (parens)."
DASHED = "Starts a run - waits - then stops."


def _tree(tmp_path: Path) -> Tree:
    t = Tree(tmp_path)
    t.section("", "Home", children=[("guide", "")])
    t.section("guide", "Guide", children=[
        ("plain", ""),
        ("described", "A durable, versioned file system."),
        ("bracketed", BRACKETS),
        ("dashed", DASHED),
        ("https://example.com/spec", "An external spec."),
    ])
    t.leaf("guide/plain", "Plain", headings=["First", "Second"])
    t.leaf("guide/described", "Described", headings=["Overview"])
    t.leaf("guide/bracketed", "Bracketed", headings=["Overview"])
    t.leaf("guide/dashed", "Dashed", headings=["Overview"])
    return t


def test_a_child_with_a_description_keeps_it(tmp_path):
    _tree(tmp_path).enhance()
    listing = Tree(tmp_path).listing("guide")

    assert "- [described](described.md) - A durable, versioned file system." in listing


def test_a_child_with_no_description_is_unchanged(tmp_path):
    _tree(tmp_path).enhance()
    lines = Tree(tmp_path).listing("guide").splitlines()

    assert "- [plain](plain.md)" in lines
    assert not any(l.startswith("- [plain](plain.md) ") for l in lines)


def test_headings_still_render_under_every_entry(tmp_path):
    _tree(tmp_path).enhance()
    lines = Tree(tmp_path).listing("guide").splitlines()

    i = lines.index("- [plain](plain.md)")
    assert lines[i + 1:i + 3] == ["  - First", "  - Second"]

    j = lines.index("- [described](described.md) - A durable, versioned file system.")
    assert lines[j + 1] == "  - Overview"


def test_a_description_holding_brackets_a_link_and_parens_is_not_truncated(tmp_path):
    """The old regex stopped at the first `)`, so everything from there on was
    lost."""
    _tree(tmp_path).enhance()
    lines = Tree(tmp_path).listing("guide").splitlines()

    assert f"- [bracketed](bracketed.md) - {BRACKETS}" in lines
    assert not any(l.startswith("- [the API]") for l in lines)


def test_a_description_containing_a_dash_separator_survives_whole(tmp_path):
    _tree(tmp_path).enhance()
    lines = Tree(tmp_path).listing("guide").splitlines()

    assert f"- [dashed](dashed.md) - {DASHED}" in lines


def test_an_external_child_keeps_its_description(tmp_path):
    """The external short-circuit runs before the heading lookup, so it needs
    the description handed to it separately."""
    _tree(tmp_path).enhance()
    lines = Tree(tmp_path).listing("guide").splitlines()

    assert "- [https://example.com/spec](https://example.com/spec) - An external spec." in lines


def test_entry_count_is_preserved(tmp_path):
    """Whatever else the rewrite does, no entry may appear or vanish."""
    t = _tree(tmp_path)
    before = [l for l in t.listing("guide").splitlines() if l.startswith("- [")]
    t.enhance()
    after = [l for l in t.listing("guide").splitlines() if l.startswith("- [")]

    assert len(before) == len(after) == 5


def test_a_link_inside_a_description_is_not_read_as_a_child_page(tmp_path):
    """`extract_subpage_links()` drives bundle contents and the consolidated
    doc. A description's own link is not a subpage and must not enter it.

    `Options - [the API](api.md)` is the shape that breaks an unanchored
    regex: the ` - ` in front of the link makes it look exactly like a listing
    entry, so the description invents a child page that does not exist."""
    t = _tree(tmp_path)
    b = LLMDocBuilder(t.base, quiet=True)
    links = b.extract_subpage_links(t.listing("guide"))

    assert links == ["plain.md", "described.md", "bracketed.md", "dashed.md"]


def test_the_llms_txt_pipe_format_carries_no_description(tmp_path):
    """A decision, not an oversight: the pipe format's delimiter is a
    character a description may contain, and nothing calls this path."""
    b = LLMDocBuilder(tmp_path, quiet=True)

    assert b.format_subpage_entry(
        "T", "u", [], as_index=True, description="d") == "T|u"
