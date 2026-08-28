#!/usr/bin/env python3
"""The `description` and `icon` frontmatter the API generator emits (DOC-1508).

A generated API-reference page has a name and four identical structural
headings, and nothing else that tells a reader what the module is. A REST API
gets away with that -- `POST /v1/charges` describes itself -- but `flyte.io`
does not. The description comes from the module or class docstring, which is
material we already have.

Two failure modes this file exists to hold shut:

1. A TRUNCATED description. Splitting a docstring on newlines cuts `flyte.io`
   mid-sentence; splitting it on every period cuts `flyte.ai` after "flyte.".
   Either produces a fragment that renders on the parent page of every reader
   passing through, which is worse than no description at all.
2. An INVALID icon. Shoelace resolves `icon` against Bootstrap Icons on the
   CDN; an unknown name 404s and renders an EMPTY SLOT with no build error and
   nothing the link checker can see (DOC-1444).

Run standalone or under pytest:
    uv run tests/test_api_generator_frontmatter.py
    uv run pytest tests/test_api_generator_frontmatter.py
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.generate.docstring import docstring_description  # noqa: E402
from lib.generate.hugo import write_front_matter, yaml_quote  # noqa: E402
from lib.generate import hugo  # noqa: E402
from lib.generate.icons import (  # noqa: E402
    PAGE_ICONS,
    class_icon_kind,
    icon_for,
    icon_set,
    validate_icon,
)
from lib.generate.site import section_root_doc  # noqa: E402


# --------------------------------------------------------------------------
# First SENTENCE, not first line
# --------------------------------------------------------------------------

def test_sentence_spanning_a_line_break_is_kept_whole():
    """The `flyte.io` case, verbatim from the shipped 2.6.10 docstring.

    Its opening sentence wraps after "... in python to", and it is preceded by a
    markdown heading. Anything line-oriented emits either "## IO data types" or
    the fragment ending in "to".
    """
    doc = (
        "## IO data types\n"
        "\n"
        "This package contains additional data types beyond the primitive data types in python to\n"
        "abstract data flow of large datasets in Union.\n"
        "\n"
        "The data types are:\n"
    )
    assert docstring_description(doc) == (
        "This package contains additional data types beyond the primitive data types "
        "in python to abstract data flow of large datasets in Union."
    )


def test_dotted_module_name_in_prose_is_not_a_sentence_end():
    # Four shipped modules open this way. Splitting on every period gives "flyte."
    assert docstring_description("flyte.ai — AI utilities for Flyte.") == \
        "flyte.ai — AI utilities for Flyte."
    assert docstring_description("Agent protocol for the flyte.ai.agents module.") == \
        "Agent protocol for the flyte.ai.agents module."


def test_version_number_is_not_a_sentence_end():
    assert docstring_description("Task Notifications API for Flyte 2.0") == \
        "Task Notifications API for Flyte 2.0."


def test_only_the_first_sentence_survives():
    assert docstring_description("Do a thing. And then another.") == "Do a thing."
    assert docstring_description("Summary line.\n\nMore detail here.") == "Summary line."


def test_leading_heading_alert_and_fence_are_skipped():
    assert docstring_description("# Syncify Module\nThis module provides `syncify`.") == \
        "This module provides `syncify`."
    assert docstring_description('!!! note "X"\n\nActual summary.') == "Actual summary."
    assert docstring_description("> [!WARNING]\n> experimental\n\nReal summary.") == \
        "Real summary."
    assert docstring_description("```python\nx = 1\n```\n\nReal summary.") == "Real summary."
    # RST underlined heading.
    assert docstring_description("Storage\n=======\n\nReal summary.") == "Real summary."


def test_code_span_periods_are_protected():
    assert docstring_description(
        "Checkpoint helper using `flyte.io.File` for all blob I/O."
    ) == "Checkpoint helper using `flyte.io.File` for all blob I/O."
    assert docstring_description(
        "A :class:`flyte.report.Timeline` that defaults to the ``Agent`` tab."
    ) == "A :class:`flyte.report.Timeline` that defaults to the ``Agent`` tab."


def test_abbreviation_is_not_a_sentence_end():
    assert docstring_description("Filter for time-based fields, e.g. Created At and Updated At.") == \
        "Filter for time-based fields, e.g. Created At and Updated At."


def test_unpunctuated_label_gets_one_period_and_no_dangling_colon():
    assert docstring_description("Artifacts module") == "Artifacts module."
    # A trailing colon introduces a list or code block that is not coming along.
    assert docstring_description(
        "Container image specification built using a fluent, two-step pattern:"
    ) == "Container image specification built using a fluent, two-step pattern."


def test_unbounded_prose_is_dropped_rather_than_truncated():
    # No terminal punctuation anywhere and too long to be a label: there is no
    # honest place to end it, so nothing is emitted.
    assert docstring_description("word " * 60) is None


# --------------------------------------------------------------------------
# Degrade honestly: no docstring -> no key
# --------------------------------------------------------------------------

def test_no_docstring_yields_none_not_empty_string():
    for empty in (None, "", "   ", "\n\n"):
        assert docstring_description(empty) is None
    # A docstring that is nothing but a heading has no prose to describe it.
    assert docstring_description("# Storage\n") is None


def test_absent_description_omits_the_key_entirely():
    out = io.StringIO()
    write_front_matter("flyte.storage", out, description=None, icon="box-seam")
    assert "description:" not in out.getvalue()
    assert 'description: ""' not in out.getvalue()
    assert "icon: box-seam" in out.getvalue()


def test_present_description_is_a_quoted_yaml_scalar():
    yaml = pytest.importorskip("yaml")
    out = io.StringIO()
    write_front_matter(
        "flyte.io", out,
        description='He said "no": a\\path # not a comment',
        icon="box-seam",
    )
    body = out.getvalue()
    parsed = yaml.safe_load(body.split("---")[1])
    assert parsed["description"] == 'He said "no": a\\path # not a comment'
    assert parsed["icon"] == "box-seam"
    assert parsed["title"] == "flyte.io"


def test_yaml_quote_escapes_backslash_and_quote():
    assert yaml_quote('a "b" c') == '"a \\"b\\" c"'
    assert yaml_quote("a\\b") == '"a\\\\b"'


# --------------------------------------------------------------------------
# Icons: every emitted name must exist, checked as it is written
# --------------------------------------------------------------------------

def test_every_page_kind_icon_exists_in_the_vendored_set():
    names = icon_set()
    assert len(names) > 1500, "vendored Bootstrap set looks truncated"
    for kind, name in PAGE_ICONS.items():
        assert name in names, f"{kind} icon {name!r} is not a Bootstrap Icons name"
        assert icon_for(kind) == name


def test_an_unknown_icon_raises_rather_than_rendering_blank():
    # The DOC-1444 names: real icons, wrong vocabulary (Lucide, not Bootstrap).
    for lucide in ("git-branch", "package-2", "settings", "zap"):
        with pytest.raises(ValueError):
            validate_icon(lucide)
    with pytest.raises(ValueError):
        out = io.StringIO()
        write_front_matter("x", out, icon="not-an-icon")


def test_unknown_page_kind_raises():
    with pytest.raises(KeyError):
        icon_for("module-ish")


def test_class_kind_splits_exception_protocol_and_class():
    assert class_icon_kind({"is_exception": True, "parent": None}) == "exception"
    assert class_icon_kind({"is_exception": False, "parent": "Protocol"}) == "protocol"
    assert class_icon_kind({"is_exception": False, "parent": "object"}) == "class"
    assert icon_for(class_icon_kind({"is_exception": True})) == "exclamation-triangle"


# --------------------------------------------------------------------------
# The section landing page
# --------------------------------------------------------------------------

def test_section_description_comes_from_the_root_package():
    source = {
        "packages": [
            {"name": "flyte.io", "doc": "IO."},
            {"name": "flyte", "doc": "Flyte SDK for authoring workflows."},
            {"name": "flyte.remote", "doc": "Remote."},
        ],
    }
    assert section_root_doc(source) == "Flyte SDK for authoring workflows."


def test_section_without_a_single_root_gets_no_description():
    source = {"packages": [{"name": "alpha", "doc": "A."}, {"name": "beta", "doc": "B."}]}
    assert section_root_doc(source) is None
    assert section_root_doc({"packages": []}) is None


def test_section_root_without_a_docstring_yields_no_description():
    # flyteplugins.ray, exactly: one package, no module docstring.
    source = {"packages": [{"name": "flyteplugins.ray", "doc": None}]}
    assert docstring_description(section_root_doc(source)) is None


# --------------------------------------------------------------------------
# The frontmatter block as a whole
# --------------------------------------------------------------------------

def test_frontmatter_field_order_and_existing_fields_survive():
    yaml = pytest.importorskip("yaml")
    hugo.set_version("2.6.10")
    hugo.set_variants(["flyte", "union"])
    out = io.StringIO()
    write_front_matter(
        "Flyte SDK", out,
        {"weight": 4, "expand_sidebar": True},
        description="Flyte SDK for authoring compound AI applications.",
        icon="book",
    )
    body = out.getvalue()
    keys = [ln.split(":")[0] for ln in body.split("---")[1].strip().split("\n")]
    assert keys == ["title", "description", "icon", "version", "variants",
                    "layout", "weight", "sidebar_expanded"]
    parsed = yaml.safe_load(body.split("---")[1])
    assert parsed["version"] == "2.6.10"
    assert parsed["variants"] == "+flyte +union"
    assert parsed["layout"] == "py_api"
    assert parsed["weight"] == 4


# --------------------------------------------------------------------------
# check_icon_names.py sees the frontmatter, not only the shortcodes
# --------------------------------------------------------------------------

def _run_checker(tmp_path, page_body):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import check_icon_names

    content = tmp_path / "content"
    content.mkdir()
    (content / "page.md").write_text(page_body, encoding="utf-8")
    argv = sys.argv
    sys.argv = ["check_icon_names.py", str(content)]
    try:
        return check_icon_names.main()
    finally:
        sys.argv = argv


def test_checker_rejects_a_dead_frontmatter_icon(tmp_path, capsys):
    # `zap` is a Lucide name. It is not in Bootstrap, so <sl-icon> renders blank.
    rc = _run_checker(tmp_path, "---\ntitle: X\nicon: zap\n---\n\nbody\n")
    assert rc == 1
    assert "icon: zap" in capsys.readouterr().out


def test_checker_accepts_a_real_frontmatter_icon_and_ignores_the_body(tmp_path):
    page = "---\ntitle: X\nicon: box-seam\n---\n\nicon: zap is prose here\n"
    assert _run_checker(tmp_path, page) == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-ra"]))
