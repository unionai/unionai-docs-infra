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

A fourth once the size cap exists: **the two kinds of abridgement must stay
distinguishable.** "More beneath it" and "cut because this file was too big"
lead somewhere different, and a manifest that blurs them tells a reader the
wrong thing about what the link is worth following for.
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
    assert "1 is summarised to its landing page, because it has more beneath it:" in bundle
    assert "> deep." in bundle
    assert "size limit" not in bundle, "nothing here was cut on size"
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


# --------------------------------------------------------------------------
# Size cap (DOC-1502 round 2). The structural rule alone does not bound size:
# a section whose children are each ONE large page is inlined whole, because
# there is no deeper `_section.md` to link onward to. `tutorials/agents` is
# that shape and came out at 1,246K against a design that expected 263K.
# --------------------------------------------------------------------------


def _fat_tree(tmp_path: Path, sizes: dict[str, int]) -> Tree:
    """A section whose children are single-page sub-sections of given byte sizes.

    The `tutorials/agents` shape: nothing structural to abridge, so only the size
    rule can bring the bundle down.
    """
    t = Tree(tmp_path)
    names = list(sizes)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=names)
    for name, size in sizes.items():
        t.section(f"guide/{name}", name.title(), "x" * size)
    return t


def _build(t: Tree, limit: int) -> str:
    b = t.builder()
    b.bundle_size_limit = limit
    b.generate_bundles("union")
    return t.bundle("guide")


def test_a_bundle_under_the_limit_is_left_alone(tmp_path):
    t = _fat_tree(tmp_path, {"alpha": 1000, "beta": 1000})
    bundle = _build(t, 200 * 1024)

    assert "abridged on size" not in bundle
    assert "size limit" not in bundle
    assert "This bundle contains 2 sub-sections. 2 are included in full." in bundle
    assert bundle.count("x" * 1000) == 2


def test_over_the_limit_the_largest_child_is_cut_first(tmp_path):
    t = _fat_tree(tmp_path, {"small": 1000, "huge": 20000, "middle": 5000})
    bundle = _build(t, 12 * 1024)

    assert "guide/huge (abridged on size)" in bundle
    assert "x" * 20000 not in bundle
    # The two that fit are untouched.
    assert "x" * 5000 in bundle
    assert "x" * 1000 in bundle
    assert len(bundle.encode("utf-8")) <= 12 * 1024


def test_cutting_continues_until_it_fits(tmp_path):
    t = _fat_tree(tmp_path, {"a": 9000, "b": 8000, "c": 7000})
    bundle = _build(t, 10 * 1024)

    assert bundle.count("abridged on size") == 2
    assert "guide/a (abridged on size)" in bundle
    assert "guide/b (abridged on size)" in bundle
    assert "x" * 7000 in bundle, "the smallest child still fits"
    assert len(bundle.encode("utf-8")) <= 10 * 1024


def test_the_sections_own_pages_are_never_cut(tmp_path):
    """A bundle that cannot fit is emitted over the limit and says so.

    Dropping the section's own content to hit a byte target would make the file
    lie about what it is. Going over, and saying so, does not.
    """
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "G" * 9000, children=["own", "kid"])
    t.leaf("guide/own", "Own", "O" * 9000)
    t.section("guide/kid", "Kid", "K" * 9000)
    bundle = _build(t, 4 * 1024)

    assert "G" * 9000 in bundle, "the landing page survives"
    assert "O" * 9000 in bundle, "the section's own leaf page survives"
    assert "K" * 9000 not in bundle, "the child was cut"
    assert "STILL over its 4 KB size limit" in bundle
    assert len(bundle.encode("utf-8")) > 4 * 1024


def test_manifest_separates_the_two_kinds_of_abridgement(tmp_path):
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=["fat", "deep", "small"])
    t.section("guide/fat", "Fat", "F" * 30000)
    t.section("guide/deep", "Deep", "Deep intro.", children=["inner"])
    t.leaf("guide/deep/inner", "Inner", "Inner body.")
    t.section("guide/small", "Small", "Small body.")
    bundle = _build(t, 8 * 1024)

    assert "This bundle contains 3 sub-sections. 1 is included in full." in bundle
    assert "1 is summarised to its landing page, because it has more beneath it:" in bundle
    assert "> deep." in bundle
    assert "1 is cut to a short excerpt, because this bundle reached its 8 KB size limit:" in bundle
    assert "> fat." in bundle


def test_a_size_cut_child_gets_one_note_carrying_both_routes(tmp_path):
    """A child can be structurally AND size abridged. Two notes would read as
    two destinations, so they reconcile into one line."""
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=["deep"])
    t.section("guide/deep", "Deep", "D" * 30000, children=["inner"])
    t.leaf("guide/deep/inner", "Inner", "Inner body.")
    bundle = _build(t, 4 * 1024)

    note = f"→ Full page: {BASE}/guide/deep/page.md · Full section: {BASE}/guide/deep/_section.md"
    assert note in bundle
    assert bundle.count("→ Full") == 1


def test_the_excerpt_prefers_the_frontmatter_description(tmp_path):
    t = _fat_tree(tmp_path, {"fat": 30000})
    src = t.content / "guide" / "fat" / "_index.md"
    src.write_text("---\ntitle: Fat\ndescription: What this page is for.\n---\n",
                   encoding="utf-8")
    bundle = _build(t, 4 * 1024)

    assert "What this page is for." in bundle


def test_the_excerpt_falls_back_to_the_first_paragraph(tmp_path):
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=["fat"])
    t.section("guide/fat", "Fat", "The opening paragraph.\n\n" + "x" * 30000)
    bundle = _build(t, 4 * 1024)

    assert "The opening paragraph." in bundle
    assert "x" * 30000 not in bundle


def test_abridgement_is_deterministic_on_equal_sized_children(tmp_path):
    """Ties break by path, so the same content always cuts the same children."""
    t = _fat_tree(tmp_path, {"zulu": 8000, "alpha": 8000, "mike": 8000})
    first = _build(t, 12 * 1024)
    second = _build(t, 12 * 1024)

    assert first == second
    assert "guide/alpha (abridged on size)" in first
    assert "guide/mike (abridged on size)" in first
    assert "x" * 8000 in first, "one child still fits"


def test_every_size_cut_child_keeps_a_route_to_its_full_text(tmp_path):
    t = _fat_tree(tmp_path, {"a": 9000, "b": 8000, "c": 7000})
    bundle = _build(t, 10 * 1024)

    for name in ("a", "b"):
        assert f"→ Full page: {BASE}/guide/{name}/page.md" in bundle


def test_a_section_with_no_sub_sections_still_reports_going_over(tmp_path):
    """The silent case: nothing to cut, so the cap cannot be met.

    `user-guide/apps/build-apps` is 8 own leaf pages and 351K, with no
    sub-sections at all. The overflow note used to live inside the
    has-sub-sections branch, so this shape emitted an over-limit bundle whose
    manifest claimed nothing was abridged and said nothing about the size.
    """
    t = Tree(tmp_path)
    t.section("", "Home", "Root.", children=["guide"])
    t.section("guide", "Guide", "Guide intro.", children=["one", "two"])
    t.leaf("guide/one", "One", "1" * 9000)
    t.leaf("guide/two", "Two", "2" * 9000)
    bundle = _build(t, 4 * 1024)

    assert "It has no sub-sections, so nothing is abridged." in bundle
    assert "This bundle is over its 4 KB size limit and has no sub-sections to abridge." in bundle
    assert "1" * 9000 in bundle and "2" * 9000 in bundle
