#!/usr/bin/env python3
"""Guard the three things a hand-authored card set opts out of.

Hand-authored cards are a permanent, first-class choice (Peeter, 2026-08-30):
the shortcode is for pages that have none, and a page that already places its
cards by hand keeps them. That is the whole point of an explicit marker.

But choosing hand-authored opts out of two things generation would have done for
free, so the check does them instead:

  coverage  a card set does not grow by itself, so a child added later has no
            card and is reachable only from the sidebar
  sync      a card body or icon can drift from what the child says about itself,
            and a reader is then told one thing on the landing page and another
            on the page

Sync starts strict because the corpus is clean: 72 of 72 card bodies and 44 of
44 icons already agree, since DOC-1510 lifted the descriptions out of the cards.
Coverage starts with a baseline because 14 children already have no card.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_subpage_cards import (  # noqa: E402
    _attr, children_of, description_of, frontmatter, icon_of,
)


def page(d: Path, name: str, **fm):
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}: {v}\n" for k, v in fm.items())
    (d / f"{name}.md").write_text(f"---\n{body}---\n\n# {name}\n")


def test_attr_reads_shortcode_parameters():
    blob = ' target="tasks" icon="gear" title="Tasks" '
    assert _attr(blob, "target") == "tasks"
    assert _attr(blob, "icon") == "gear"
    assert _attr(blob, "missing") == ""


def test_children_counts_dirs_and_leaves_but_not_the_index(tmp_path):
    (tmp_path / "sub").mkdir()
    page(tmp_path, "_index")
    page(tmp_path, "leaf")
    assert children_of(tmp_path) == {"sub", "leaf"}


def test_description_and_icon_are_read_from_either_page_shape(tmp_path):
    page(tmp_path, "leaf", title="Leaf", description="A leaf page.", icon="gear")
    page(tmp_path / "sect", "_index", title="Sect", description="A section.", icon="box")
    assert description_of(tmp_path, "leaf") == "A leaf page."
    assert icon_of(tmp_path, "leaf") == "gear"
    assert description_of(tmp_path, "sect") == "A section."
    assert icon_of(tmp_path, "sect") == "box"


def test_a_quoted_description_loses_its_quotes(tmp_path):
    page(tmp_path, "leaf", title="Leaf", description="'123 things.'")
    assert description_of(tmp_path, "leaf") == "123 things."


def test_absent_values_are_empty_not_none(tmp_path):
    """An empty string means 'nothing to compare', which is not a mismatch."""
    page(tmp_path, "leaf", title="Leaf")
    assert description_of(tmp_path, "leaf") == ""
    assert icon_of(tmp_path, "leaf") == ""


def test_a_child_that_does_not_exist_is_not_a_mismatch(tmp_path):
    """A card may target a sibling or an external page; that is not this check."""
    assert description_of(tmp_path, "nowhere") == ""
    assert icon_of(tmp_path, "nowhere") == ""


def test_frontmatter_stops_at_the_closing_fence(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("---\ntitle: X\n---\n\ndescription: not front matter\n")
    assert "not front matter" not in frontmatter(p.read_text())


def test_two_siblings_sharing_an_icon_is_detectable(tmp_path):
    """The clash a reader sees: two cards side by side, identical icons.

    check_icon_names.py cannot catch this. Every name involved is perfectly
    valid; the problem is that they are the same. 14 sets existed before this,
    including eight cloud-provider pages all carrying `cloud`, so a grid of
    eight showed one icon eight times and distinguished nothing.
    """
    page(tmp_path / "a", "_index", title="A", icon="cloud")
    page(tmp_path / "b", "_index", title="B", icon="cloud")
    page(tmp_path / "c", "_index", title="C", icon="server")
    seen = {}
    for name in sorted(children_of(tmp_path)):
        i = icon_of(tmp_path, name)
        if i:
            seen.setdefault(i, []).append(name)
    dupes = {i: n for i, n in seen.items() if len(n) > 1}
    assert dupes == {"cloud": ["a", "b"]}


def test_a_child_with_no_icon_is_not_a_clash(tmp_path):
    """Absence is not sameness; two iconless siblings are fine."""
    page(tmp_path / "a", "_index", title="A")
    page(tmp_path / "b", "_index", title="B")
    seen = {}
    for name in sorted(children_of(tmp_path)):
        i = icon_of(tmp_path, name)
        if i:
            seen.setdefault(i, []).append(name)
    assert not {i: n for i, n in seen.items() if len(n) > 1}


def test_a_child_opts_out_of_its_card(tmp_path):
    """`card_enabled: false` on the CHILD suppresses that page's card only."""
    from check_subpage_cards import card_disabled
    page(tmp_path, "quiet", title="Quiet", card_enabled="false")
    page(tmp_path, "loud", title="Loud")
    page(tmp_path, "explicit", title="Explicit", card_enabled="true")
    assert card_disabled(tmp_path, "quiet")
    assert not card_disabled(tmp_path, "loud"), "absent means carded"
    assert not card_disabled(tmp_path, "explicit"), "true means carded"


def test_the_opt_out_reads_the_child_not_the_parent(tmp_path):
    """It is a property of the page being carded, not of the page doing the carding."""
    from check_subpage_cards import card_disabled
    (tmp_path / "_index.md").write_text("---\ntitle: P\ncard_enabled: false\n---\n")
    page(tmp_path, "child", title="Child")
    assert not card_disabled(tmp_path, "child"), "the parent's key must not suppress a child"


def test_there_is_no_parent_side_frontmatter_opt_out(tmp_path):
    """`subpage_cards: false` is gone. A page needing no cards goes in the baseline.

    One file holds every exemption rather than two mechanisms doing the same job.
    """
    import check_subpage_cards as m
    assert not hasattr(m, "OPT_OUT")
    assert "subpage_cards" not in m.CARD_DISABLED.pattern
