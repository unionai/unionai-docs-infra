#!/usr/bin/env python3
"""The identity block at the top of every page twin, and the line it depends on.

A twin is the one docs surface where ANY page can be the entry point. An agent
arriving at `user-guide/tasks.md` from a search result sees a description and a
title, and cannot tell Union from Flyte, v2 from v1, or where the index is. The
block says so in five bullets.

Two things here are easy to get wrong and expensive to get wrong quietly:

  * WHICH BLOCK. There are four, one per (line, variant), and they differ in
    ways that matter. Telling a v1 reader "these are the v2 docs" would be false
    exactly where an agent most needs correcting.

  * WHICH LINE a version belongs to. A build makes several trees per line --
    `latest`, the stable pin, every enumerated tag -- and they are all one line.
    Comparing the version string to "v2" has been shipping a wrong banner on
    `/docs/latest`: "This is legacy (latest) documentation. Do not use." On the
    current docs.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import PAGE_PREAMBLES, version_line  # noqa: E402


# --- which line a version belongs to ---------------------------------------

@pytest.mark.parametrize("version", ["v2", "latest", "v2.6.5.0", "v2.6.10.0", "v2.99.0.0"])
def test_the_v2_line_includes_latest_and_every_pin(version):
    """The regression: `latest` and the pins are the CURRENT line, not legacy."""
    assert version_line(version) == "v2"


@pytest.mark.parametrize("version", ["v1", "v1.16.28.2", "v1.0.0.0"])
def test_the_v1_line_includes_its_pins(version):
    assert version_line(version) == "v1"


def test_latest_is_not_legacy():
    """Stated on its own because it is what was live and wrong.

    /docs/latest/union/llms.txt told agents the current docs were legacy and not
    to use them, which is the most-linked index on the site.
    """
    assert version_line("latest") == "v2"


# --- the four blocks --------------------------------------------------------

ALL = [("v2", "union"), ("v2", "flyte"), ("v1", "union"), ("v1", "flyte")]


def test_there_is_a_block_for_every_line_and_variant():
    assert set(PAGE_PREAMBLES) == set(ALL)


@pytest.mark.parametrize("key", ALL)
def test_every_block_has_the_same_five_bullets(key):
    b = PAGE_PREAMBLES[key]
    assert len(b) == 5
    assert all(x.startswith("* ") for x in b)


@pytest.mark.parametrize("key", ALL)
def test_a_block_names_its_own_line_and_variant(key):
    line, variant = key
    first = PAGE_PREAMBLES[key][0]
    assert line in first, first
    assert ("Union.ai" if variant == "union" else "Flyte") in first, first


@pytest.mark.parametrize("key", ALL)
def test_bullet_4_crosses_the_variant_axis_only(key):
    """It points at the other product on the SAME line."""
    line, variant = key
    other = "flyte" if variant == "union" else "union"
    assert f"/docs/{line}/{other}/llms.txt" in PAGE_PREAMBLES[key][3]


@pytest.mark.parametrize("key", ALL)
def test_bullet_5_crosses_the_version_axis_only(key):
    """It points at the other line for the SAME product."""
    line, variant = key
    other = "v1" if line == "v2" else "v2"
    assert f"/docs/{other}/{variant}/llms.txt" in PAGE_PREAMBLES[key][4]


@pytest.mark.parametrize("key", ALL)
def test_bullet_3_says_what_the_relationship_means(key):
    """Not just that Union is a superset, but what follows for the reader.

    Three of the four said it and one only stated the fact, which left that one
    block telling an agent something true and nothing to do about it.
    """
    assert ", so " in PAGE_PREAMBLES[key][2], PAGE_PREAMBLES[key][2]


@pytest.mark.parametrize("key", ALL)
def test_a_v1_block_tells_the_reader_to_prefer_v2(key):
    line, _ = key
    last = PAGE_PREAMBLES[key][4]
    if line == "v1":
        assert "v1 is out of date and deprecated" in last
        assert "/docs/v2/" in last
    else:
        assert "These are the v2 docs" in last


@pytest.mark.parametrize("key", ALL)
def test_no_v1_block_claims_a_shared_sdk(key):
    """v1 shares `flytekit` but NOT the CLI (`pyflyte` vs `uctl`).

    v2 can say "the same `flyte` SDK and CLI" because it is simply true. On v1 it
    is messier, and a preamble is the wrong place to unpick it, so the v1 blocks
    name no SDK at all.
    """
    line, _ = key
    joined = " ".join(PAGE_PREAMBLES[key])
    if line == "v1":
        assert "SDK" not in joined, joined
    else:
        assert "`flyte` SDK and CLI" in joined


@pytest.mark.parametrize("key", ALL)
def test_every_url_is_canonical_www(key):
    """Bare union.ai 302s, costing a redirect hop in ~2,900 files."""
    for bullet in PAGE_PREAMBLES[key]:
        assert "https://union.ai/" not in bullet, bullet
