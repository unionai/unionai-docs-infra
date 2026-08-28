#!/usr/bin/env python3
"""Rendering regressions in tools/api_generator (DOC-1323, DOC-1383).

Three defects the generated API reference carried, each of which a reader
could see on the page:

1. HTML escaping ran over inline code spans, so `->` inside backticks reached
   the reader as a literal `-&gt;`. Entities do not decode inside code.
2. Parameter defaults were dropped from rendered signatures, so nothing
   distinguished a required argument from an optional one.
3. The `*` of a vararg was written into the Type column instead of the name,
   destroying the real type.

Escaping still has to protect the page, so the collateral cases are here too:
a literal `<` in prose, an HTML tag in prose, and a `|` inside a table cell.

Run standalone or under pytest:
    uv run tests/test_api_generator_rendering.py
    uv run pytest tests/test_api_generator_rendering.py
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.generate.methods import (  # noqa: E402
    escape_html_preserve_code_blocks,
    format_type,
    generate_method_decl,
    generate_params,
    param_display_name,
)
from lib.parser.methods import format_default  # noqa: E402


def _param(name, type="", kind="POSITIONAL_OR_KEYWORD", default=None, doc=None):
    return {"name": name, "type": type, "kind": kind, "default": default, "doc": doc}


def _method(params, params_doc=None, return_type="None"):
    return {
        "name": "f",
        "doc": None,
        "signature": "",
        "params": params,
        "params_doc": params_doc,
        "return_type": return_type,
        "return_doc": None,
        "raises": None,
        "notes": None,
        "framework": "python",
        "parent_name": None,
    }


def _decl(method, name="f", **kwargs):
    out = io.StringIO()
    generate_method_decl(name, method, out, **kwargs)
    return out.getvalue()


def _params(method):
    out = io.StringIO()
    generate_params(method, out)
    return out.getvalue()


# --- 1. inline code spans survive escaping (DOC-1323) ------------------------


def test_arrow_in_inline_span_is_not_escaped():
    assert escape_html_preserve_code_blocks("call `a -> b` now") == "call `a -> b` now"


def test_arrow_in_double_backtick_span_is_not_escaped():
    text = "Callback ``(model, msgs) -> LLMMessage``."
    assert escape_html_preserve_code_blocks(text) == text


def test_fenced_block_still_survives():
    text = "before\n```python\nx: dict[str, T] = {}\n```\nafter"
    assert escape_html_preserve_code_blocks(text) == text


def test_adjacent_spans_do_not_swallow_the_prose_between_them():
    got = escape_html_preserve_code_blocks("See `<a>` then x < y then `<b>` done.")
    assert got == "See `<a>` then x &lt; y then `<b>` done."


def test_blockquote_marker_is_preserved_and_its_content_escaped():
    got = escape_html_preserve_code_blocks("> use `Dict[str, T]` when T <: object")
    assert got == "> use `Dict[str, T]` when T &lt;: object"


# --- collateral: escaping still protects the page ---------------------------


def test_angle_brackets_in_prose_are_still_escaped():
    got = escape_html_preserve_code_blocks("form <registry>/<name>:<tag>")
    assert got == "form &lt;registry&gt;/&lt;name&gt;:&lt;tag&gt;"


def test_html_tag_in_prose_is_still_escaped():
    got = escape_html_preserve_code_blocks("wrap it in <script>alert(1)</script>")
    assert "<script>" not in got
    assert got == "wrap it in &lt;script&gt;alert(1)&lt;/script&gt;"


def test_an_unbalanced_backtick_falls_back_to_escaping():
    got = escape_html_preserve_code_blocks("a stray ` tick and a <T> after it")
    assert got == "a stray ` tick and a &lt;T&gt; after it"


def test_a_pipe_in_a_description_stays_escaped_so_the_table_holds():
    method = _method(
        [_param("pat", "str")],
        params_doc={"pat": {"doc": "Either `a|b` or <all>; see the | form."}},
    )
    row = [ln for ln in _params(method).split("\n") if ln.startswith("| `pat`")][0]
    # three cells, so the pipes inside the description did not split the row
    assert row.count("|") - row.count("\\|") == 4
    assert "&lt;all&gt;" in row
    assert "`a\\|b`" in row


def test_a_tag_left_inside_a_code_span_is_inert_because_it_is_code():
    # The generator hands `<script>` through untouched inside a span; the
    # markdown renderer escapes it when it emits the <code> element, so the
    # page shows the text and never a live tag. Verified against Hugo in the
    # DOC-1383 run; asserted here at the generator boundary.
    got = escape_html_preserve_code_blocks("sanitize `<script>x</script>` first")
    assert got == "sanitize `<script>x</script>` first"


# --- 2. defaults reach the rendered signature (DOC-1383) --------------------


def test_string_default_is_quoted_in_the_signature():
    method = _method([_param("name", "str", default=format_default("flyte-agent"))])
    assert "name: str = 'flyte-agent'," in _decl(method, "Agent", is_class=True)


def test_a_parameter_without_a_default_gets_none():
    method = _method([_param("key", "str")])
    assert "key: str,\n" in _decl(method)


def test_an_explicit_none_default_is_rendered():
    method = _method([_param("org", "str | None", default=format_default(None))])
    assert "org: str | None = None," in _decl(method)


def test_format_default_quotes_strings_and_leaves_scalars_alone():
    assert format_default("x") == "'x'"
    assert format_default(True) == "True"
    assert format_default(25) == "25"
    assert format_default(()) == "()"


def test_format_default_names_a_function_rather_than_its_address():
    def _default_call_llm():
        pass

    assert format_default(_default_call_llm) == "_default_call_llm"


def test_format_default_gives_up_rather_than_emitting_an_address():
    class Opaque:
        pass

    assert format_default(Opaque()) == "..."


# --- 3. varargs render as a name, not a type (DOC-1383) ---------------------


def test_var_positional_gets_its_star_on_the_name():
    assert param_display_name(_param("envs", "Environment", "VAR_POSITIONAL")) == "*envs"


def test_var_keyword_gets_two_stars_even_when_not_called_kwargs():
    assert param_display_name(_param("kwds", "", "VAR_KEYWORD")) == "**kwds"


def test_an_ordinary_parameter_called_args_keeps_its_type():
    method = _method([_param("args", "dict[str, Any]")])
    row = [ln for ln in _params(method).split("\n") if ln.startswith("| `args`")][0]
    assert row == "| `args` | `dict[str, Any]` | |"


def test_a_vararg_row_carries_its_real_type():
    method = _method([_param("envs", "Environment", "VAR_POSITIONAL")])
    row = [ln for ln in _params(method).split("\n") if ln.startswith("| `*envs`")][0]
    assert row == "| `*envs` | `Environment` | |"


def test_an_unannotated_vararg_leaves_the_type_cell_empty():
    method = _method([_param("args", "", "VAR_POSITIONAL")])
    row = [ln for ln in _params(method).split("\n") if ln.startswith("| `*args`")][0]
    assert row == "| `*args` |  | |"


def test_format_type_no_longer_reads_the_parameter_name():
    assert format_type("dict[str, Any]") == "`dict[str, Any]`"
    assert format_type("<class 'flyte.io._file.File'>") == "`flyte.io._file.File`"
    assert format_type("") == ""


if __name__ == "__main__":
    failures = 0
    for key, fn in sorted(dict(globals()).items()):
        if not key.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"ok   {key}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {key}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)


# ---------------------------------------------------------------------------
# RST cross-reference roles (DOC-1452)
#
# convert_rst_roles turns Sphinx roles into code spans, which the site then
# autolinks via the generated linkmaps. Its domain prefix was hardcoded to
# `py:`, so an explicit `std` domain matched only the role tail and left an
# orphaned `:std` in reader-facing prose:
#
#     :std:ref:`flyte:divedeep-workflows`  ->  :std`flyte:divedeep-workflows`
#
# 30 occurrences across the v1 reference (`:std:ref:` x29, `:std:doc:` x1).
# The prefix now matches any domain, so a domain nobody has used yet cannot
# reintroduce this.
# ---------------------------------------------------------------------------

from lib.parser.docstring import convert_rst_roles  # noqa: E402


def test_std_domain_role_is_converted():
    """The regression. Was ':std`flyte:divedeep-workflows`'."""
    assert (
        convert_rst_roles(":std:ref:`flyte:divedeep-workflows`")
        == "`flyte:divedeep-workflows`"
    )


def test_std_doc_role_is_converted():
    assert convert_rst_roles(":std:doc:`/user_guide/index`") == "`/user_guide/index`"


def test_unprefixed_role_still_works():
    assert convert_rst_roles(":ref:`label`") == "`label`"


def test_py_domain_still_works():
    assert convert_rst_roles(":py:class:`pkg.X`") == "`pkg.X`"


def test_an_unused_domain_does_not_regress():
    """The point of matching any domain: a new one must not need a code change."""
    assert convert_rst_roles(":cpp:class:`Foo`") == "`Foo`"


def test_tilde_shortening_survives_a_domain_prefix():
    assert convert_rst_roles(":py:class:`~pkg.mod.Name`") == "`Name`"


def test_explicit_title_survives_a_domain_prefix():
    assert (
        convert_rst_roles(":std:ref:`Some Title <pkg.mod.Name>`") == "`Some Title`"
    )


def test_role_inside_a_code_fence_is_left_alone():
    text = "```\n:std:ref:`flyte:divedeep-workflows`\n```"
    assert convert_rst_roles(text) == text


def test_prose_around_a_role_is_preserved():
    assert (
        convert_rst_roles("Please read :std:ref:`flyte:divedeep-workflows` first")
        == "Please read `flyte:divedeep-workflows` first"
    )
