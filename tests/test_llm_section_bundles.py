#!/usr/bin/env python3
"""Guard the shape of the `_section.md` bundles (DOC-1502).

Three properties are easy to break and expensive to notice, because a bundle
that is wrong still looks like a bundle:

  * **A childless section must emit nothing.** Its content already lives at its
    own page URL and its parent inlines it in full, so a bundle there is a
    byte-identical third copy.
  * **The manifest counts must be true.** The header is the part that survives a
    truncated fetch. An agent that knows it holds 18 of 21 sub-sections can go
    and get the rest; one that is told the wrong number answers from what it has
    and never mentions the gap.
  * **An onward link sits next to the content it abridges**, never in a trailing
    block. At a 100K fetch cap a trailing block lost a third of all onward links,
    from files that still read as complete.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import LLMDocBuilder  # noqa: E402

BASE = "https://www.union.ai/docs/v2/union"


class Tree:
    """A source `content/` tree and its built `dist/` twin, kept in step.

    Both are needed: the built tree cannot tell a childless section from a leaf
    page (`dir/page.md` either way), so the generator asks the source tree, and
    so must the fixture.
    """

    def __init__(self, tmp_path: Path):
        self.base = tmp_path
        self.content = tmp_path / "content"
        self.out = tmp_path / "dist" / "docs" / "v2" / "union"

    def section(self, rel: str, title: str, body: str = "", children: list[str] = ()):
        """A Hugo section: `_index.md` in source, `page.md` in the built tree."""
        src = self.content / rel if rel else self.content
        src.mkdir(parents=True, exist_ok=True)
        (src / "_index.md").write_text(f"---\ntitle: {title}\n---\n", encoding="utf-8")

        page = f"# {title}\n\n{body}\n"
        if children:
            page += "\n## Subpages\n"
            for child in children:
                page += f"- [{child}]({child}/page.md)\n"
        self._write_out(rel, page)

    def leaf(self, rel: str, title: str, body: str = ""):
        """A leaf page: `foo.md` in source, `foo/page.md` in the built tree."""
        src = self.content / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.with_suffix(".md").write_text(
            f"---\ntitle: {title}\n---\n", encoding="utf-8")
        self._write_out(rel, f"# {title}\n\n{body}\n")

    def _write_out(self, rel: str, text: str):
        f = (self.out / rel / "page.md") if rel else (self.out / "page.md")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def builder(self) -> LLMDocBuilder:
        b = LLMDocBuilder(self.base, quiet=True)
        b.version = "v2"
        b.variant_root = self.out
        return b

    def bundle(self, rel: str) -> str:
        return (self.out / rel / "_section.md").read_text(encoding="utf-8")

    def has_bundle(self, rel: str) -> bool:
        return (self.out / rel / "_section.md").exists()


def _leaf_only_tree(tmp_path: Path) -> Tree:
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=["alpha", "beta"])
    t.leaf("guide/alpha", "Alpha", "Alpha body.")
    t.leaf("guide/beta", "Beta", "Beta body.")
    return t


def _mixed_tree(tmp_path: Path) -> Tree:
    """A section with one thin sub-section and one that continues deeper."""
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=["thin", "deep"])
    t.section("guide/thin", "Thin", "Thin intro.")
    t.section("guide/deep", "Deep", "Deep intro.", children=["inner"])
    t.leaf("guide/deep/inner", "Inner", "Inner body.")
    return t


def test_section_with_only_leaf_children_inlines_them_all(tmp_path):
    t = _leaf_only_tree(tmp_path)
    t.builder().generate_bundles("union")

    bundle = t.bundle("guide")
    assert "Alpha body." in bundle
    assert "Beta body." in bundle
    assert "Full section:" not in bundle
    assert "Includes this section's landing page and 2 of its own pages, in full." in bundle
    assert "It has no sub-sections, so nothing is abridged." in bundle


def test_single_page_section_emits_nothing(tmp_path):
    """`integrations/hydra` shaped: one `_index.md`, nothing beneath it."""
    t = Tree(tmp_path)
    t.section("integrations", "Integrations", "", children=["hydra"])
    t.section("integrations/hydra", "Hydra", "Hydra body.")
    t.builder().generate_bundles("union")

    assert t.has_bundle("integrations")
    assert not t.has_bundle("integrations/hydra")


def test_manifest_counts_full_versus_summarised_and_names_the_summarised(tmp_path):
    t = _mixed_tree(tmp_path)
    t.builder().generate_bundles("union")

    bundle = t.bundle("guide")
    assert "This bundle contains 2 sub-sections. 1 is included in full." in bundle
    assert "1 is summarised here with a link to its full section:" in bundle
    assert "> deep." in bundle
    # The abridged child is present as its landing page only.
    assert "Deep intro." in bundle
    assert "Inner body." not in bundle


def test_onward_link_follows_its_own_child_not_the_end_of_the_file(tmp_path):
    t = _mixed_tree(tmp_path)
    t.builder().generate_bundles("union")

    lines = t.bundle("guide").splitlines()
    link = next(i for i, l in enumerate(lines) if l.startswith("→ Full section:"))
    deep = next(i for i, l in enumerate(lines) if l == "Deep intro.")
    thin = next(i for i, l in enumerate(lines) if l == "Thin intro.")

    assert lines[link] == f"→ Full section: {BASE}/guide/deep/_section.md"
    assert deep < link, "the link must follow the child it abridges"
    assert link > thin, "children keep their order; the link is not hoisted"
    # Nothing but the link's own blank line after it -- no trailing block.
    assert [l for l in lines[link + 1:] if l.strip()] == []


def test_a_childless_child_gets_no_onward_link(tmp_path):
    t = _mixed_tree(tmp_path)
    t.builder().generate_bundles("union")

    bundle = t.bundle("guide")
    assert f"{BASE}/guide/thin/_section.md" not in bundle
    assert bundle.count("→ Full section:") == 1


def test_link_to_a_page_the_bundle_carries_becomes_a_title(tmp_path):
    t = _leaf_only_tree(tmp_path)
    t.leaf("guide/alpha", "Alpha", "See [Beta](../beta/page.md).")
    b = t.builder()
    b.build_lookup_tables(t.out, "page.md", t.out, [])
    b.generate_bundles("union")

    assert "See **Home > Guide > Beta**." in t.bundle("guide")


def test_link_deeper_than_the_bundle_stays_a_url(tmp_path):
    """The one-level rule's sharp edge.

    Under whole-subtree bundling every link below the section was inlined, so
    replacing it with a bold title lost nothing. One level down, a link into a
    child's subtree points at content this file does NOT carry -- bolding it
    would delete the only route there.
    """
    t = _mixed_tree(tmp_path)
    t.section("guide/deep", "Deep", "See [Inner](inner/page.md).", children=["inner"])
    b = t.builder()
    b.build_lookup_tables(t.out, "page.md", t.out, [])
    b.generate_bundles("union")

    bundle = t.bundle("guide")
    assert f"[Inner]({BASE}/guide/deep/inner/page.md)" in bundle
    assert "**Inner**" not in bundle


def test_bundle_urls_are_registered_for_llms_txt(tmp_path):
    t = _mixed_tree(tmp_path)
    b = t.builder()
    b.generate_bundles("union")

    assert b.bundle_sections["guide"] == f"{BASE}/guide/_section.md"
    assert b.bundle_sections["guide/deep"] == f"{BASE}/guide/deep/_section.md"
    assert "guide/thin" not in b.bundle_sections


def test_subpages_table_is_not_repeated_inside_the_bundle(tmp_path):
    t = _leaf_only_tree(tmp_path)
    t.builder().generate_bundles("union")

    assert "## Subpages" not in t.bundle("guide")
