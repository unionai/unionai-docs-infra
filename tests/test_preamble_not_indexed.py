#!/usr/bin/env python3
"""The twin preamble must never reach Algolia.

BOTH indices are built from the same `dist/**/<path>.md` twins:

    build_records.py          ->  `union`           keyword, read by DocSearch
    build_markdown_records.py ->  `union-markdown`  retrieval, read by Ask AI

So a block prepended to every twin is a block prepended to every record, unless
something stops it. What stops it is that `parse_sections()` discards everything
before the first heading. The page description already lives there and is
already absent from both indices; the preamble sits in the same place.

That is an IMPLICIT property, and an expensive one to lose. If `parse_sections`
ever starts keeping pre-heading content, roughly 1,277 retrieval records each
gain ~800 bytes of identical boilerplate: the real content becomes a smaller
fraction of every chunk, and every page looks more like every other page to the
embedding. Nothing would fail; retrieval would just quietly get worse.

Hence these tests. They are about Algolia, not about the preamble, and they live
here so the reason is written down next to the assertion.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "algolia_indexer"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_records import parse_sections  # noqa: E402
from build_llm_docs import PAGE_PREAMBLES  # noqa: E402

PREAMBLE = "\n".join(PAGE_PREAMBLES[("v2", "union")])

PAGE = PREAMBLE + """

Configure, build, and deploy the durable batch workloads that everything else is made of.

# Tasks

A task is a Python function that runs remotely in a container.

## Defining a task

Decorate a function with `@env.task`.
"""


def indexed_text(md):
    return "\n".join("\n".join(s.get("body") or []) for s in parse_sections(md))


def test_no_preamble_text_is_indexed():
    body = indexed_text(PAGE)
    assert "part of the Union.ai v2 docs" not in body
    assert "commercial superset" not in body
    assert "llms.txt" not in body


def test_the_page_description_is_not_indexed_either():
    """Same mechanism, and it is the proof this is existing behaviour."""
    assert "durable batch workloads" not in indexed_text(PAGE)


def test_the_real_content_still_is():
    body = indexed_text(PAGE)
    assert "A task is a Python function" in body
    assert "Decorate a function" in body


def test_the_preamble_does_not_become_a_section_of_its_own():
    """A phantom section would get its own retrieval record on every page."""
    titles = [s["title"] for s in parse_sections(PAGE)]
    assert titles == ["Tasks", "Defining a task"], titles


def test_the_first_indexed_section_is_the_page_title():
    """The primary record for a page must still be keyed to its H1."""
    first = parse_sections(PAGE)[0]
    assert first["level"] == 1 and first["title"] == "Tasks"


def test_the_bullets_are_not_mistaken_for_content_in_any_variant():
    for key, block in PAGE_PREAMBLES.items():
        md = "\n".join(block) + "\n\n# Title\n\nReal body.\n"
        body = indexed_text(md)
        assert "Full index" not in body, key
        assert "Real body." in body, key
