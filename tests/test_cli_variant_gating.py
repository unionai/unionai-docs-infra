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
    load_variant_map,
    parse_all_variants,
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


def test_gen_command_supplies_only_the_variant_universe():
    # The per-distribution policy deliberately does NOT come from here. Reading it
    # back out of the generator's own arguments would make the check agree with the
    # generator by construction, which is how a note-based check passed on #1483.
    assert parse_all_variants("flyte gen docs --type markdown --plugin-variants 'union'") == ALL
    assert parse_all_variants("flyte gen docs --variants 'flyte union oss'") == frozenset(
        {"flyte", "union", "oss"}
    )
    assert parse_all_variants("flyte gen docs --variants='flyte union'") == ALL


def test_variant_map_accepts_a_bare_string_or_a_list(tmp_path):
    f = tmp_path / "cli-variants.toml"
    f.write_text(
        "[distributions]\n"
        'flyteplugins-union = "union"\n'
        'flyteplugins-hydra = ["flyte", "union"]\n'
    )
    assert load_variant_map(f) == {
        "flyteplugins-union": UNION,
        "flyteplugins-hydra": ALL,
    }


def test_missing_variant_map_is_not_an_error(tmp_path):
    # An absent file leaves every distribution unlisted, which constrains nothing.
    # The check must not become a hard dependency on a file that may not be there.
    assert load_variant_map(tmp_path / "nope.toml") == {}


def test_listed_distribution_uses_its_configured_variants():
    m = {"flyteplugins-union": UNION, "flyteplugins-hydra": ALL}
    assert expected_variants("flyteplugins-union", m, ALL) == UNION
    assert expected_variants("flyteplugins-hydra", m, ALL) == ALL


def test_unlisted_distribution_falls_through_to_every_variant():
    # The chosen default. An unclassified plugin is NOT assumed Union-only: that
    # would hide an open-source plugin from the readers who can run it, which is
    # the DOC-1478 conflation pointing the other way. check_cli() names what it
    # skipped, so the gap is visible without failing the regen.
    assert expected_variants("flyteplugins-newthing", {"flyteplugins-union": UNION}, ALL) == ALL
    assert expected_variants(None, {"flyteplugins-union": UNION}, ALL) == ALL


# --- check_cli: both directions, and the one it must stay silent about ---------

import check_cli_variant_gating as mod  # noqa: E402

PAGE = """
{{< variant union >}}
### flyte fork
{{< /variant >}}

### flyte hydra
"""


def _run(tmp_path, monkeypatch, variant_map, eps):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "probe_entry_points", lambda _python: eps)
    (tmp_path / "page.md").write_text(PAGE)
    unlisted: set[str] = set()
    cli = {"output_file": "page.md", "gen_command": "flyte gen docs --plugin-variants union"}
    return mod.check_cli(cli, Path("/unused"), variant_map, unlisted), unlisted


def test_leak_is_reported(tmp_path, monkeypatch):
    # `flyte hydra` is ungated, so it renders for every reader. Claiming it is
    # union-only must fail: that is the DOC-1478 shape.
    problems, _ = _run(
        tmp_path, monkeypatch,
        {"flyteplugins-hydra": UNION},
        [{"name": "hydra", "dist": "flyteplugins-hydra"}],
    )
    assert len(problems) == 1
    assert "EVERY variant" in problems[0]


def test_wrongly_hidden_is_reported(tmp_path, monkeypatch):
    # The mirror image. `flyte fork` is gated to union while the map says its
    # distribution ships to both, so Flyte readers lose a command they can run.
    # Unreachable before the map existed: policy came from the generator, so the
    # check agreed with whatever the generator did.
    problems, _ = _run(
        tmp_path, monkeypatch,
        {"flyteplugins-union": ALL},
        [{"name": "fork", "dist": "flyteplugins-union"}],
    )
    assert len(problems) == 1
    assert "hidden from variant(s) 'flyte'" in problems[0]


def test_correct_gating_is_silent(tmp_path, monkeypatch):
    problems, _ = _run(
        tmp_path, monkeypatch,
        {"flyteplugins-union": UNION},
        [{"name": "fork", "dist": "flyteplugins-union"}],
    )
    assert problems == []


def test_unlisted_distribution_is_recorded_but_not_enforced(tmp_path, monkeypatch):
    problems, unlisted = _run(
        tmp_path, monkeypatch, {}, [{"name": "fork", "dist": "flyteplugins-union"}]
    )
    assert problems == []
    assert unlisted == {"flyteplugins-union"}
