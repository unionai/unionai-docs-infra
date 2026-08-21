#!/usr/bin/env python3
"""Guard the CLI doc driver's failure paths (DOC-1481 step 3).

`generate_python_cli` used to warn and return on a non-zero exit, and `main`
used to skip quietly when the API venv was missing. Both left the previously
generated page in place, so a broken generator and a missing environment both
looked exactly like a clean regen.

The subtler case is a generator that exits 0 and renders almost nothing. The
tree-walk descends through private SDK types -- `FileGroup` by isinstance,
`TaskFiles` and `RemoteTaskGroup` by class-name STRING -- so an upstream rename
does not raise. The walk simply stops descending and the page comes out short.
`count_commands` plus the two floors exist to make that loud.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from api_cli_generate import (  # noqa: E402
    MIN_COMMANDS,
    MIN_RETAINED_FRACTION,
    count_commands,
)


def test_counts_command_sections_at_every_depth():
    md = "\n".join(
        [
            "## flyte",
            "### flyte run",
            "#### flyte get api-key",
            "##### flyte update policy",
        ]
    )
    assert count_commands(md, "flyte") == 4


def test_ignores_headings_that_are_not_commands():
    md = "\n".join(
        [
            "# Flyte CLI",
            "## Union-specific functionality {#plugin-commands}",
            "### flyte run",
        ]
    )
    assert count_commands(md, "flyte") == 1


def test_does_not_match_a_longer_command_name_by_prefix():
    # `flytectl` is not `flyte`; a prefix match would inflate the count and
    # defeat the floor it feeds.
    assert count_commands("### flytectl get", "flyte") == 0


def test_ignores_command_names_in_prose_and_code():
    md = "\n".join(["Run `flyte run` to submit.", "    ### flyte run", "### flyte run"])
    # Only the real heading at column 0 counts.
    assert count_commands(md, "flyte") == 1


def test_counts_a_different_cli_by_its_own_name():
    md = "### uctl get\n### flyte run"
    assert count_commands(md, "uctl") == 1


def test_empty_output_counts_zero_and_is_below_the_floor():
    assert count_commands("", "flyte") == 0
    assert count_commands("", "flyte") < MIN_COMMANDS


def test_floors_are_set_to_catch_a_collapse_not_normal_churn():
    # A release removing a command or two must not trip this; a walk that
    # stopped descending must.
    assert MIN_COMMANDS >= 10
    assert 0.0 < MIN_RETAINED_FRACTION < 1.0
    healthy, collapsed = 93, 4
    assert collapsed < healthy * MIN_RETAINED_FRACTION
    assert 90 >= healthy * MIN_RETAINED_FRACTION  # losing 3 of 93 is fine
