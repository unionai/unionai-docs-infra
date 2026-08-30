#!/usr/bin/env python3
"""Guard the generated-link checker, especially version-agnostic baselining.

The first version of the baseline was keyed on the FULL url path, version
segment included (`v2/union/x`). It passed locally and failed in CI, because a
local `make dist` builds two trees and CI builds ten: `latest`, the stable line
and three pinned tags, each times two variants. The same content defect appears
once per tree, so 32 exclusions matched and 72 links failed.

Version-agnostic matching is also the only form that works for a pinned tag at
all. Those trees are built from an immutable tag, so a link broken inside one can
never be fixed there -- excluding it by content path is the only option, and
excluding it by version would need a new entry at every cut, forever.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "link_checker"))

from check_generated_links import (  # noqa: E402
    build_scope, in_scope, looks_like_code, resolves, without_version,
)


def test_the_version_segment_is_dropped():
    assert without_version("v2/union/api-reference/page.md") == "union/api-reference/page.md"
    assert without_version("latest/flyte/x") == "flyte/x"
    assert without_version("v2.6.5.0/union/x/y") == "union/x/y"


def test_every_version_tree_matches_one_baseline_entry():
    """The regression: one entry must cover the same defect in all ten trees."""
    entry = "union/api-reference/page.md"
    for version in ("v2", "latest", "v2.6.5.0", "v2.6.6.0", "v2.6.9.0"):
        assert without_version(f"{version}/{entry}") == entry


def test_the_variant_is_kept():
    """A union-only defect must not be excused in the flyte tree."""
    assert without_version("v2/union/x") != without_version("v2/flyte/x")


def test_a_bare_path_survives():
    assert without_version("union") == "union"


def test_code_samples_are_recognised():
    for u in ("https://www.union.ai/docs/v2/f/**args",
              "https://www.union.ai/docs/v2/f/x=1,",
              "https://www.union.ai/docs/v2/f/`localhost`",
              "https://www.union.ai/docs/v2/f/{{< x >}}"):
        assert looks_like_code(u), u


def test_a_real_url_is_not_mistaken_for_code():
    for u in ("https://www.union.ai/docs/v2/union/user-guide/tasks.md",
              "https://www.union.ai/docs/v2/union/a-b_c/d.md#anchor"):
        assert not looks_like_code(u), u


def test_scope_and_resolution(tmp_path):
    dist = tmp_path / "docs"
    (dist / "v2" / "union").mkdir(parents=True)
    (dist / "latest" / "union").mkdir(parents=True)
    (dist / "v2" / "union" / "page.md").write_text("x")
    (dist / "v2" / "union" / "sect").mkdir()

    scope = build_scope(dist)
    assert scope == {"v2/union", "latest/union"}
    assert in_scope("v2/union/page.md", scope)
    assert not in_scope("v1/union/page.md", scope), "v1 is not in this build"
    assert not in_scope("v2", scope), "a bare version is not a page"

    assert resolves(dist, "v2/union/page.md")
    assert resolves(dist, "v2/union/sect"), "a directory resolves"
    assert resolves(dist, "v2/union/page"), "the twin resolves without .md"
    assert not resolves(dist, "v2/union/nope")
