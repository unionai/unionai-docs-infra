#!/usr/bin/env python3
"""RST literal blocks become fenced code; module docstrings get the same cleanup.

Two gaps left by the free-form section fix (render_custom_sections):

1. RST literal blocks. `Typical use::` followed by a blank line and an
   indented block is RST for "the block below is code". Only `Example::` was
   handled (rewritten to feed the Examples section); every other `Foo::` kept
   its stray `::` and its block stayed an unhighlighted indented code block --
   or, nested in a list item, ran together into one line of prose
   (`flyteplugins.agents.google.FlyteLLM.generate_content_async`).

2. Module docstrings. They were written to the page with `cleandoc` alone,
   bypassing everything `parse_docstring` does -- free-form sections, RST
   roles, admonitions -- so `flyte.notify` rendered four sections as code.

Run standalone or under pytest:
    uv run tests/test_api_docstring_literal_blocks.py
    uv run pytest tests/test_api_docstring_literal_blocks.py
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.parser.docstring import (  # noqa: E402
    convert_rst_literal_blocks,
    parse_docstring,
    render_module_docstring,
)


def _doc(text: str) -> str:
    return textwrap.dedent(text).strip("\n")


# --- RST literal blocks -------------------------------------------------------


def test_literal_block_becomes_a_python_fence():
    doc = _doc('''
        Typical use::

            from flyte.remote import Task
            Task.get("x")
    ''')
    assert convert_rst_literal_blocks(doc) == _doc('''
        Typical use:

        ```python
        from flyte.remote import Task
        Task.get("x")
        ```
    ''')


def test_space_before_the_marker_drops_it_entirely():
    doc = "Run this ::\n\n    x = 1"
    assert convert_rst_literal_blocks(doc) == "Run this\n\n```python\nx = 1\n```"


def test_a_bare_marker_line_is_dropped():
    doc = "Some prose.\n\n::\n\n    x = 1"
    assert convert_rst_literal_blocks(doc) == "Some prose.\n\n```python\nx = 1\n```"


def test_a_block_that_is_not_python_gets_a_bare_fence():
    doc = "Output::\n\n    Blob(image/png, 1.6MB)"
    assert convert_rst_literal_blocks(doc) == "Output:\n\n```\nBlob(image/png, 1.6MB)\n```"


def test_placeholder_code_that_does_not_parse_is_still_python():
    """`...` after keyword arguments is a SyntaxError, but the block is Python."""
    doc = "Bind it::\n\n    chat = App(name=\"x\", ...)"
    assert convert_rst_literal_blocks(doc).startswith("Bind it:\n\n```python\n")


def test_a_literal_block_in_a_list_item_stays_in_the_item():
    doc = _doc('''
        1. Simple text streaming::

             f(1)
             f(2)

           Note: parts merge.
        2. Text + function call::

             g()
    ''')
    assert convert_rst_literal_blocks(doc) == _doc('''
        1. Simple text streaming:

           ```python
           f(1)
           f(2)
           ```

           Note: parts merge.
        2. Text + function call:

           ```python
           g()
           ```
    ''')


def test_an_rst_auto_numbered_item_keeps_its_paragraph_out_of_the_block():
    """`#.` is a list marker: the block ends where the item's text resumes."""
    doc = _doc('''
        #. One needs to be able to call ::

              x = t1().with_overrides(...)

           If the task returns an integer, this throws.
        #. Next.
    ''')
    out = convert_rst_literal_blocks(doc)
    assert "   ```python\n   x = t1().with_overrides(...)\n   ```\n\n   If the task returns" in out


def test_relative_indentation_inside_the_block_survives():
    doc = "Usage::\n\n    def f():\n        return 1"
    assert convert_rst_literal_blocks(doc) == "Usage:\n\n```python\ndef f():\n    return 1\n```"


def test_an_rst_directive_is_not_a_literal_block():
    doc = ".. note::\n\n    Be careful."
    assert convert_rst_literal_blocks(doc) == doc


def test_a_marker_without_an_indented_block_is_left_alone():
    doc = "Call it like so::\n\nNothing indented here."
    assert convert_rst_literal_blocks(doc) == doc


def test_a_marker_inside_a_fence_is_left_alone():
    doc = "```cpp\nstd::\n\n    x\n```"
    assert convert_rst_literal_blocks(doc) == doc


def test_parse_docstring_fences_literal_blocks_in_the_description():
    info = parse_docstring(_doc('''
        Save a checkpoint.

        Use it with the run launcher::

            run = flyte.with_runcontext(env_vars=env).run(task)
    '''), source=None)
    assert "Use it with the run launcher:\n\n```python\nrun = flyte" in info["docstring"]


def test_example_double_colon_still_feeds_the_examples_section():
    info = parse_docstring(_doc('''
        Do a thing.

        Example::

            do_thing()
    '''), source=None)
    assert info["examples"].strip() == "do_thing()"
    assert "```" not in info["docstring"]


def test_a_literal_block_inside_a_free_form_section():
    """Fenced first, so its assignment no longer makes the body read as code."""
    info = parse_docstring(_doc('''
        A volume.

        Checkpointing:
            Save each epoch with the trace pattern::

                ckpt = load()
                save(ckpt)
    '''), source=None)
    assert info["docstring"].strip().endswith(_doc('''
        **Checkpointing:**

        Save each epoch with the trace pattern:

        ```python
        ckpt = load()
        save(ckpt)
        ```
    '''))


# --- Table cells --------------------------------------------------------------

from lib.generate.methods import table_cell  # noqa: E402


def test_a_fence_in_a_table_cell_becomes_one_code_span():
    """Flattened as-is, the fence became a span opening with the word `python`."""
    cell = table_cell("Returns if READY. Usage:\n\n```python\np = Promise()\nif p.is_ready():\n    print(p.val)\n```")
    assert cell == "Returns if READY. Usage:  `p = Promise() if p.is_ready(): print(p.val)`"


def test_a_pipe_in_a_table_cell_is_still_escaped():
    assert table_cell("a | b\nc") == "a \\| b c"


# --- Module docstrings --------------------------------------------------------

NOTIFY = '''Task Notifications API.

Quick Start:
    ```python
    from flyte import notify
    ```

Supported Phases:
    - SUCCEEDED: Task completed successfully
    - FAILED: Task failed

See :class:`~flyte.notify.Email`.
'''


def test_module_docstring_gets_free_form_sections():
    out = render_module_docstring(NOTIFY)
    assert "**Quick Start:**\n\n```python\nfrom flyte import notify\n```" in out
    assert "**Supported Phases:**\n\n- SUCCEEDED: Task completed successfully\n- FAILED" in out


def test_module_docstring_gets_rst_roles_converted():
    assert "See `Email`." in render_module_docstring(NOTIFY)


def test_module_example_literal_block_is_fenced_not_stripped():
    out = render_module_docstring("A module.\n\nExample::\n\n    import flyte\n")
    assert out == "A module.\n\nExample:\n\n```python\nimport flyte\n```"


def test_module_docstring_none_passes_through():
    assert render_module_docstring(None) is None
    assert render_module_docstring("") == ""


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
