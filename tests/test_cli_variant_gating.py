#!/usr/bin/env python3
"""Guard the CLI variant-gating check against the two ways it could be useless.

The check exists because a Union-only CLI command leaked into the open-source
variant of the generated reference (DOC-1479, unionai-docs#1483). Two properties
make it worth having, and both are easy to lose in a refactor:

  * It must cover SUBCOMMANDS. Only 4 of the 35 CLI entry points
    `flyteplugins-union` declares are top-level; the other 31 are dotted names
    like `get.api-key` that attach to a CORE verb group. A check that only looks
    at top-level `### flyte <name>` headings covers 11% of the surface while
    reporting success.
  * It must key on the DISTRIBUTION, not on "is a plugin". `flyteplugins-hydra`
    ships inside flyte-sdk to open-source users, so a plugin/core boolean would
    hide an OSS command from the Flyte docs --- the same conflation, reversed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_cli_variant_gating import (  # noqa: E402
    expected_variants,
    gating_by_heading,
    heading_for,
    parse_gen_command,
)

ALL = frozenset({"flyte", "union"})
UNION = frozenset({"union"})


def test_top_level_entry_point_maps_to_its_heading():
    assert heading_for("fork") == "flyte fork"


def test_dotted_entry_point_maps_to_a_subcommand_heading():
    # The 31-of-35 case. Getting this wrong is how a check passes while blind.
    assert heading_for("get.api-key") == "flyte get api-key"
    assert heading_for("create.role") == "flyte create role"


def test_only_the_first_dot_separates_group_from_command():
    assert heading_for("get.my.object") == "flyte get my.object"


def test_gating_reads_the_enclosing_variant():
    md = "\n".join(
        [
            "{{< variant union >}}",
            "### flyte fork",
            "{{< /variant >}}",
            "### flyte run",
        ]
    )
    got = gating_by_heading(md)
    assert got["flyte fork"] == UNION
    assert got["flyte run"] is None


def test_gating_tracks_nesting_rather_than_pairing_tags():
    md = "\n".join(
        [
            "{{< variant union >}}",
            "{{< markdown >}}",
            "#### flyte get api-key",
            "{{< /markdown >}}",
            "{{< /variant >}}",
            "### flyte run",
        ]
    )
    got = gating_by_heading(md)
    assert got["flyte get api-key"] == UNION
    # The block closed, so the next command must not inherit its gate.
    assert got["flyte run"] is None


def test_subcommand_headings_are_found_at_any_depth():
    md = "\n".join(["##### flyte update policy", "## flyte get"])
    got = gating_by_heading(md)
    assert "flyte update policy" in got
    assert "flyte get" in got


def test_gen_command_supplies_the_policy():
    default, overrides, all_variants = parse_gen_command(
        "flyte gen docs --type markdown --plugin-variants 'union'"
    )
    assert default == UNION
    assert overrides == {}
    assert all_variants == ALL


def test_gen_command_reads_per_plugin_overrides():
    # Once flyteorg/flyte-sdk#1464 lands, the pipeline can map an OSS plugin to
    # both variants; the check has to follow the same policy or it will report
    # the corrected output as a leak.
    default, overrides, _ = parse_gen_command(
        "flyte gen docs --plugin-variants union "
        "--plugin-variant-map 'flyteplugins.hydra=flyte union'"
    )
    assert default == UNION
    assert overrides == {"flyteplugins.hydra": ALL}


def test_gen_command_accepts_equals_form():
    default, _, all_variants = parse_gen_command("flyte gen docs --plugin-variants=union --variants='flyte union'")
    assert default == UNION
    assert all_variants == ALL


def test_distribution_maps_to_its_module_prefix():
    overrides = {"flyteplugins.hydra": ALL}
    assert expected_variants("flyteplugins-hydra", UNION, overrides) == ALL
    assert expected_variants("flyteplugins-union", UNION, overrides) == UNION


def test_unmapped_distribution_uses_the_blanket_value():
    assert expected_variants("flyteplugins-union", UNION, {}) == UNION


def test_unknown_distribution_is_not_assumed_union_only():
    # A distribution nobody mapped still gets the configured default rather than
    # a hardcoded assumption, so adding a plugin cannot silently change meaning.
    assert expected_variants(None, ALL, {}) == ALL
