#!/usr/bin/env python3
"""Guard frontmatter `icon:` parsing in check_icon_names.py.

The checker gained a frontmatter arm in DOC-1508, because the API generator now
writes an `icon:` on every page it emits and a bad name is a blank slot with no
build error. The arm stripped double quotes only:

    fm_icon = re.compile(r'^icon:\\s*"?([^"\\n]*?)"?\\s*$')

so `icon: '123'` captured `'123'`, quotes included, and failed. That is the one
form the author had no choice about: `123` is a real Bootstrap icon, and written
bare YAML reads it as the integer 123. The check rejected correct input and the
error line echoed the captured value, which read as though the file were wrong.

It ran green on the PR that introduced the page and red on the next one, because
that PR is what raised the infra pin -- the checker and the page it rejects
arrived from different repos.

These tests pin all three YAML quoting forms as equivalent, since Hugo hands the
shortcode the same name for each, and keep the cases that must still fail.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_icon_names import unquote_scalar  # noqa: E402

# The pattern under test, kept in step with the one main() compiles.
FM_ICON = re.compile(r"^icon:\s*(\S.*?)\s*$")


def parse(line: str):
    """Return the icon name a frontmatter line yields, or None if it is not one."""
    m = FM_ICON.match(line)
    return unquote_scalar(m.group(1)) if m else None


def test_three_quoting_forms_are_the_same_name():
    """Bare, single and double reach the shortcode identically, so all must pass."""
    assert parse("icon: rocket") == "rocket"
    assert parse("icon: 'rocket'") == "rocket"
    assert parse('icon: "rocket"') == "rocket"


def test_numeric_name_survives_its_mandatory_quotes():
    """The regression: `123` is a real icon and MUST be quoted to stay a string."""
    assert parse("icon: '123'") == "123"
    assert parse('icon: "123"') == "123"


def test_hyphenated_and_dotted_names_are_untouched():
    assert parse("icon: box-seam") == "box-seam"
    assert parse("icon: 'arrow-right'") == "arrow-right"
    assert parse("icon: 1-circle-fill") == "1-circle-fill"


def test_surrounding_whitespace_is_stripped():
    assert parse("icon:    rocket   ") == "rocket"
    assert parse("icon: 'rocket'  ") == "rocket"


def test_a_bad_name_is_still_reported_as_written():
    """Unquoting must not repair anything -- a wrong name stays wrong and visible."""
    assert parse("icon: rockett") == "rockett"
    assert parse("icon: 'not-an-icon'") == "not-an-icon"


def test_unbalanced_quotes_are_left_alone():
    """Returned as written so the author sees the real text, not a guess at it."""
    assert parse("icon: 'rocket") == "'rocket"
    assert parse('icon: rocket"') == 'rocket"'
    assert parse("icon: \"rocket'") == "\"rocket'"


def test_empty_and_absent_values_yield_no_name():
    """An empty value emits no icon at all; it is redundant, not broken."""
    assert parse("icon:") is None
    assert parse("icon:   ") is None
    assert unquote_scalar("''") == ""
    assert unquote_scalar('""') == ""


def test_other_frontmatter_keys_are_not_icons():
    assert parse("title: rocket") is None
    assert parse("description: icon: rocket") is None


def test_unquote_scalar_strips_one_pair_only():
    """Nested quotes are content, not syntax -- strip the outer pair and stop."""
    assert unquote_scalar("\"'rocket'\"") == "'rocket'"
    assert unquote_scalar("rocket") == "rocket"
