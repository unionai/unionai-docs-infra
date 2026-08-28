#!/usr/bin/env python3
"""The renderer that turns `flyte gen docs --type json` into the CLI reference page.

This code used to live in flyte-sdk. It moved here because everything it does is
a fact about this site -- shortcode names, goldmark's limits, which audience sees
what -- and none of it is a fact about the SDK (DOC-1481). The SDK now reports
what the CLI is; the decisions are made here.

The port was verified by generating both ways from the same command tree and
diffing: byte-identical across 2759 lines apart from 39 deliberate corrections to
the plugin's name. These tests pin the behaviour that diff proved, so a later
edit cannot quietly lose it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import cli_render  # noqa: E402

ALL = frozenset({"flyte", "union"})
UNION_MAP = {"flyteplugins-union": frozenset({"union"})}


def _cmd(path, *, distribution=None, is_group=False, declares_options=False, options=None, arguments=None, help=None):
    return {
        "path": path,
        "name": path.rsplit(" ", 1)[-1],
        "is_group": is_group,
        "distribution": distribution,
        "declares_options": declares_options,
        "help": help,
        "arguments": arguments or [],
        "options": options or [{"opts": ["--help"], "secondary_opts": [], "type": "boolean", "default": False, "help": "Show this message and exit."}],
    }


def _render(commands, plugin_variants="union", variant_map=UNION_MAP):
    return cli_render.render({"cli": "flyte", "version": "0", "commands": commands}, variant_map, plugin_variants, ALL)


# --- gating ------------------------------------------------------------------


def test_a_gated_command_is_wrapped_and_a_core_one_is_not():
    out = _render([_cmd("flyte run"), _cmd("flyte fork", distribution="flyteplugins-union")])
    fork = out[out.index("### flyte fork"):]
    assert "{{< variant union >}}" in out
    assert out.count("{{< variant union >}}") >= 1
    # The core command is emitted with no wrapper: unwrapped means every reader.
    run_at = out.index("### flyte run")
    assert "{{< variant" not in out[out.rindex("\n", 0, run_at) - 200:run_at]


def test_an_unlisted_distribution_is_not_gated():
    """Falls through to every variant, so it needs no wrapper -- the mirror-image
    bug is hiding an open-source plugin from the readers who can run it."""
    out = _render([_cmd("flyte hydra", distribution="flyteplugins-hydra")], variant_map=UNION_MAP)
    assert "### flyte hydra" in out
    assert "{{< variant" not in out


def test_nothing_gated_means_no_variant_blocks_at_all():
    out = _render([_cmd("flyte run")])
    assert "{{< variant" not in out
    assert "{{< grid >}}" in out


# --- usage line and option table ---------------------------------------------


def test_declares_options_drives_both_the_usage_line_and_the_table():
    """A command with only click's `--help` gets neither. A table holding just
    that row says nothing, and `[OPTIONS]` would advertise nothing."""
    bare = _render([_cmd("flyte abort", is_group=True)])
    assert "**`flyte abort COMMAND [ARGS]...`**" in bare
    assert "| Option | Type | Default | Description |" not in bare

    real = _render([_cmd("flyte run", declares_options=True, options=[
        {"opts": ["-p", "--project"], "secondary_opts": [], "type": "text", "default": None, "help": "Project."},
    ])])
    assert "**`flyte run [OPTIONS]`**" in real
    assert "| `-p` `--project` | `text` |  | Project. |" in real


def test_arguments_render_required_and_optional_differently():
    out = _render([_cmd("flyte get run", arguments=[
        {"name": "name", "required": True}, {"name": "note", "required": False},
    ])])
    assert "**`flyte get run NAME [NOTE]`**" in out


def test_secondary_opts_are_rendered_as_aliases():
    out = _render([_cmd("flyte x", declares_options=True, options=[
        {"opts": ["--flag"], "secondary_opts": ["--no-flag"], "type": "boolean", "default": False, "help": ""},
    ])])
    assert "| `--flag` `--no-flag` |" in out


# --- Hugo's constraints, which are why this code is here at all --------------


def test_hugo_delimiters_in_help_are_escaped():
    """A CLI author may write `{{<` for their own reasons; unescaped it is parsed
    as a shortcode and fails the build."""
    out = _render([_cmd("flyte x", declares_options=True, options=[
        {"opts": ["--x"], "secondary_opts": [], "type": "text", "default": None, "help": "Use {{< thing >}} here."},
    ])])
    assert "{{&lt; thing" in out
    assert "| `--x` | `text` |  | Use {{< thing" not in out


def test_indented_help_blocks_become_fenced_code():
    """Indentation-based code does not survive `{{< markdown >}}`'s RenderString."""
    out = cli_render.format_help("Examples:\n\n    $ flyte run x.py\n\nDone.")
    assert "```bash" in out
    assert "$ flyte run x.py" in out
    assert "    $ flyte run x.py" not in out


# --- index tables ------------------------------------------------------------


def test_a_verb_with_no_subcommands_is_still_listed():
    """Dropping it makes a documented command unreachable from the index. `fork`
    builds its subcommands dynamically and is exactly this shape."""
    rows = cli_render.build_index_table({"fork": []}, {"fork": True}, True, include_gated=True)
    assert any("[`fork⁺`](#flyte-fork)" in r for r in rows)


def test_a_gated_verb_is_absent_from_the_core_table():
    """The property the whole variant split rests on."""
    rows = cli_render.build_index_table({"fork": []}, {"fork": True}, True, include_gated=False)
    assert not any("fork" in r for r in rows)


def test_the_core_index_is_wrapped_in_the_non_plugin_variants():
    out = _render([_cmd("flyte run"), _cmd("flyte fork", distribution="flyteplugins-union")])
    assert "{{< variant flyte >}}" in out


# --- policy ------------------------------------------------------------------


def test_variants_for_falls_through_when_unlisted():
    assert cli_render.variants_for("flyteplugins-hydra", UNION_MAP, ALL) == ALL
    assert cli_render.variants_for(None, UNION_MAP, ALL) == ALL
    assert cli_render.variants_for("flyteplugins-union", UNION_MAP, ALL) == frozenset({"union"})


def test_the_variant_map_accepts_a_string_or_a_list(tmp_path):
    f = tmp_path / "cli-variants.toml"
    f.write_text('[distributions]\nflyteplugins-union = "union"\nflyteplugins-x = ["flyte", "union"]\n')
    assert cli_render.load_variant_map(f) == {
        "flyteplugins-union": frozenset({"union"}),
        "flyteplugins-x": ALL,
    }
