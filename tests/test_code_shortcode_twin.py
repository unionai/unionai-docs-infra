#!/usr/bin/env python3
"""A {{< code >}} shortcode must put the same code in the twin as on the page.

The twin generator matched only `file="..." lang="..." fragment="..."`, quoted and in that
order. Any other form (the common one is `fragment=name lang=python`) lost the fragment, so
the twin inlined the whole example file, every other fragment's code and its raw
`docs-fragment` markers included: 539 of 1,059 embeds on main. Hugo reads the attributes by
name, so the page was right and only the agent surface was wrong. DOCP-95.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from process_shortcodes import (  # noqa: E402
    ShortcodeProcessor,
    parse_shortcode_attrs,
    render_code_include,
)

EXAMPLE = """\
import flyte

app_env = flyte.app.AppEnvironment(name="my-app")


# {{docs-fragment first}}
first_line = 1


second_line = 2
# {{/docs-fragment first}}

# {{docs-fragment outer}}
outer_a = 1
# {{docs-fragment inner}}
inner = 2
# {{/docs-fragment inner}}
outer_b = 3
# {{/docs-fragment outer}}
"""


def _twin(tmp_path, shortcode, example=EXAMPLE):
    f = tmp_path / "unionai-examples" / "v2" / "ex.py"
    f.parent.mkdir(parents=True)
    f.write_text(example, encoding="utf-8")
    p = ShortcodeProcessor.__new__(ShortcodeProcessor)
    p.base_path = tmp_path
    return p.process_code_shortcodes(f"before\n\n{shortcode}\n\nafter\n")


@pytest.mark.parametrize(
    "shortcode",
    [
        '{{< code file="/unionai-examples/v2/ex.py" fragment=first lang=python >}}',
        '{{< code file="/unionai-examples/v2/ex.py" lang=python fragment=first >}}',
        '{{< code file="/unionai-examples/v2/ex.py" lang="python" fragment="first" >}}',
        '{{< code file="/unionai-examples/v2/ex.py" fragment="first" lang="python" >}}',
        "{{< code lang='python' fragment='first' file='/unionai-examples/v2/ex.py' >}}",
        '{{<code file="/unionai-examples/v2/ex.py"   fragment=first   lang=python>}}',
    ],
)
def test_every_attribute_form_selects_the_fragment(tmp_path, shortcode):
    out = _twin(tmp_path, shortcode)
    assert "```python\nfirst_line = 1\n\nsecond_line = 2\n```" in out
    assert "import flyte" not in out
    assert "docs-fragment" not in out
    assert "*Source: https://github.com/unionai/unionai-examples/blob/main/v2/ex.py*" in out
    assert out.startswith("before\n") and out.endswith("after\n")


def test_no_fragment_shows_the_whole_file_without_markers(tmp_path):
    out = _twin(tmp_path, '{{< code file="/unionai-examples/v2/ex.py" lang=python >}}')
    assert "import flyte" in out and "inner = 2" in out
    assert "docs-fragment" not in out


def test_nested_markers_are_dropped_from_the_outer_fragment():
    assert render_code_include(EXAMPLE, fragment="outer") == "outer_a = 1\ninner = 2\nouter_b = 3"


def test_show_fragments_keeps_the_markers():
    out = render_code_include(EXAMPLE, fragment="outer", show_fragments=True)
    assert "# {{docs-fragment inner}}" in out


def test_from_and_to_select_lines_inclusive():
    assert render_code_include("a\nb\nc\nd\n", line_from="2", line_to="3") == "b\nc"


def test_fragment_wins_over_from_and_to():
    assert render_code_include(EXAMPLE, fragment="inner", line_from="1", line_to="2") == "inner = 2"


def test_blank_lines_trimmed_and_collapsed():
    assert render_code_include("\n\nx = 1\n\n\n\ny = 2\n\n") == "x = 1\n\ny = 2"


def test_missing_fragment_yields_no_code_not_the_whole_file():
    assert render_code_include(EXAMPLE, fragment="nope") == ""


def test_code_containing_a_fence_gets_a_longer_fence(tmp_path):
    example = '# {{docs-fragment f}}\nst.write("""\n```bash\nuv run generate.py\n```\n""")\n# {{/docs-fragment f}}\n'
    out = _twin(tmp_path, '{{< code file="/unionai-examples/v2/ex.py" fragment=f lang=python >}}', example)
    assert out.startswith("before\n\n````python\nst.write(")
    assert '```\n""")\n````\n\n*Source:' in out


def test_parse_shortcode_attrs():
    assert parse_shortcode_attrs('file="/a b.py" fragment=x lang=\'py\' from=3') == {
        "file": "/a b.py",
        "fragment": "x",
        "lang": "py",
        "from": "3",
    }
