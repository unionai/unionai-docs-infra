#!/usr/bin/env python3
"""A heading copied into a parent's listing must keep pointing at its own target.

`enhance_subpage_listings()` copies each child's H2/H3 headings into the parent's
`## Subpages` block. When a heading is itself a link, its URL was written against
the CHILD page. Copying moved it to a page with a different base, and the later
`absolutize_links()` pass then resolved it against the PARENT.

Live consequences before this fix:

    user-guide/get-started.md
      - [Core concepts](.../get-started/core-concepts.md)
        - [Leases](.../get-started/leases.md)          <- 404
                                  ^^^^ dropped core-concepts/

    deployment.md
      - Security best practices -> .../union/security  <- 200, THE WRONG PAGE

The second is the worse failure and the reason DOC-1499 exists: a 404 tells a
reader to look elsewhere, a wrong 200 does not. Four of the six 404s in a
448-target live sample came from this one function (DOC-1511 site B).

The fix resolves the link at copy time, against the child, while the child is
still known. These tests pin that, plus the cases that must not change.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import LLMDocBuilder  # noqa: E402

BASE_URL = "https://www.union.ai/docs/v2/union"


@pytest.fixture
def tree(tmp_path):
    """A variant tree with a section, a child section, and a grandchild."""
    content = tmp_path / "content"
    variant = tmp_path / "dist" / "docs" / "v2" / "union"
    for d in (content / "user-guide" / "get-started" / "core-concepts", variant / "user-guide" / "get-started" / "core-concepts"):
        d.mkdir(parents=True, exist_ok=True)

    # source tree decides section-vs-leaf, so the landings need an _index.md
    for rel in ("user-guide", "user-guide/get-started", "user-guide/get-started/core-concepts"):
        (content / rel / "_index.md").write_text("---\ntitle: x\n---\n")
    (content / "user-guide" / "get-started" / "core-concepts" / "leases.md").write_text("---\ntitle: Leases\n---\n")

    (variant / "user-guide" / "get-started" / "core-concepts" / "leases.md").write_text("# Leases\n")
    (variant / "user-guide" / "get-started" / "core-concepts.md").write_text("# Core concepts\n")

    b = LLMDocBuilder.__new__(LLMDocBuilder)
    b.base_path = tmp_path
    b.content_root = content
    b.variant_root = variant
    b.quiet = True
    return b, variant


def child_of(variant, url_dir):
    from page_paths import page_for
    return page_for(variant, url_dir)


def test_relative_heading_link_resolves_against_the_child(tree):
    """The regression: `leases` is relative to core-concepts, not to get-started."""
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    out, n = b.absolutize_in("[Leases](leases)", base, BASE_URL)
    assert out == f"[Leases]({BASE_URL}/user-guide/get-started/core-concepts/leases.md)"
    assert n == 1


def test_resolving_against_the_parent_is_what_went_wrong(tree):
    """Pin the wrong answer explicitly, so a regression is unambiguous."""
    b, variant = tree
    parent = child_of(variant, "user-guide/get-started")
    out, _ = b.absolutize_in("[Leases](leases)", parent, BASE_URL)
    assert "core-concepts" not in out
    assert "/user-guide/get-started/leases" in out


def test_dot_slash_and_bare_forms_agree(tree):
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    want = f"[Leases]({BASE_URL}/user-guide/get-started/core-concepts/leases.md)"
    assert b.absolutize_in("[Leases](./leases)", base, BASE_URL)[0] == want
    assert b.absolutize_in("[Leases](leases)", base, BASE_URL)[0] == want


def test_anchor_rides_after_the_md_suffix(tree):
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    out, _ = b.absolutize_in("[Leases](leases#renewal)", base, BASE_URL)
    assert "/core-concepts/leases.md#renewal)" in out


def test_already_absolute_links_are_untouched(tree):
    """The heading pass runs before absolutize_links, which must then be a no-op."""
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    already = f"[Leases]({BASE_URL}/user-guide/get-started/core-concepts/leases.md)"
    out, n = b.absolutize_in(already, base, BASE_URL)
    assert out == already
    assert n == 0


def test_external_and_anchor_only_links_are_untouched(tree):
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    for link in ("[x](https://example.com)", "[x](mailto:a@b.c)", "[x](#section)"):
        out, n = b.absolutize_in(link, base, BASE_URL)
        assert out == link and n == 0


def test_root_relative_links_get_the_host_only(tree):
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    out, n = b.absolutize_in("[x](/docs/v2/union/foo)", base, BASE_URL)
    assert out == "[x](https://www.union.ai/docs/v2/union/foo)"
    assert n == 1


def test_a_plain_heading_is_returned_unchanged(tree):
    """Most headings are not links; the rewrite must leave them alone."""
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    out, n = b.absolutize_in("How Flyte works", base, BASE_URL)
    assert out == "How Flyte works"
    assert n == 0


def test_count_is_reported_so_a_no_op_pass_cannot_look_successful(tree):
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    _, n = b.absolutize_in("[a](leases) and [b](leases)", base, BASE_URL)
    assert n == 2


# ---------------------------------------------------------------------------
# End-to-end. The tests above prove `absolutize_in` resolves correctly; they do
# NOT prove `enhance_subpage_listings` calls it with the child as the base. That
# distinction is exactly what a previous fix got wrong: its unit tests asserted
# on the argument handed to a function rather than on what the function built,
# and passed against code that did almost nothing. So this runs the real pass
# over a real tree and reads the emitted listing.
# ---------------------------------------------------------------------------

def build_tree(tmp_path):
    """A parent whose child carries a heading that is a link to a grandchild."""
    content = tmp_path / "content"
    variant = tmp_path / "dist" / "docs" / "v2" / "union"
    (content / "guide" / "concepts").mkdir(parents=True)
    (variant / "guide" / "concepts").mkdir(parents=True)

    for rel in ("guide", "guide/concepts"):
        (content / rel / "_index.md").write_text("---\ntitle: x\n---\n")
    (content / "guide" / "concepts" / "leases.md").write_text("---\ntitle: Leases\n---\n")

    (variant / "guide" / "concepts" / "leases.md").write_text("# Leases\n")
    (variant / "guide" / "concepts.md").write_text(
        "# Concepts\n\n## How it works\n\n### [Leases](leases)\n")
    (variant / "guide.md").write_text(
        "# Guide\n\n## Subpages\n- [Concepts](concepts/) - What the concepts are.\n")

    b = LLMDocBuilder.__new__(LLMDocBuilder)
    b.base_path = tmp_path
    b.content_root = content
    b.variant_root = variant
    b.quiet = True
    b.version = "v2"
    b.section_pages = {"guide"}
    b.page_headings = {"guide/concepts": ["How it works", "[Leases](leases)"]}
    return b, variant


def test_end_to_end_the_emitted_listing_points_at_the_grandchild(tmp_path):
    """The whole point: run the real pass, read the real output."""
    b, variant = build_tree(tmp_path)
    b.enhance_subpage_listings("union", "v2")
    out = (variant / "guide.md").read_text()

    assert f"[Leases]({BASE_URL}/guide/concepts/leases.md)" in out, out
    assert f"{BASE_URL}/guide/leases" not in out, "resolved against the parent again"


def test_end_to_end_a_plain_heading_survives_untouched(tmp_path):
    b, variant = build_tree(tmp_path)
    b.enhance_subpage_listings("union", "v2")
    out = (variant / "guide.md").read_text()
    assert "  - How it works" in out


def test_end_to_end_the_child_description_still_survives(tmp_path):
    """DOC-1508's fix must keep working through this change."""
    b, variant = build_tree(tmp_path)
    b.enhance_subpage_listings("union", "v2")
    out = (variant / "guide.md").read_text()
    assert "- [Concepts](concepts/) - What the concepts are." in out


def test_a_link_wrapped_across_lines_is_still_external(tree):
    """A markdown link may wrap, putting a newline inside the captured target.

    Left in place it defeats the scheme test, so an external URL is taken for a
    relative path and gets the docs base glued in front of it. Real case, from
    user-guide/tasks/task-programming/reports.md:

        (You can find it in the [source file](
        https://github.com/unionai/unionai-examples/blob/main/...))

    which produced
        .../union/user-guide/tasks/task-programming/https:/github.com/unionai/...
    """
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    wrapped = "[source file](\nhttps://github.com/unionai/unionai-examples/blob/main/x.py)"
    out, n = b.absolutize_in(wrapped, base, BASE_URL)
    assert "union.ai/docs" not in out, out
    assert n == 0


def test_a_wrapped_relative_link_still_resolves(tree):
    """Stripping must not stop a genuinely relative wrapped link from resolving."""
    b, variant = tree
    base = child_of(variant, "user-guide/get-started/core-concepts")
    out, n = b.absolutize_in("[Leases](\n  leases)", base, BASE_URL)
    assert out == f"[Leases]({BASE_URL}/user-guide/get-started/core-concepts/leases.md)"
    assert n == 1
