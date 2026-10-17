#!/usr/bin/env python3
"""Free-form docstring sections render as Markdown, not as code blocks.

Google-style docstrings use headers beyond the ones the parser knows
(`Args:`, `Returns:`, `Raises:`, `Note:`, `Example:`). A header such as
`Restarts and retries:` with its body indented beneath it passed through
verbatim, and the four-space indent made Markdown render the whole body as an
indented code block: numbered lists, bold, inline code and nested Python
fences all reached the reader as literal text (unionai-docs#1701, on
`flyte.clustered.ClusteredTaskEnvironment`).

The body is dedented and the header becomes a bold lead-in, the same shape as
`**Example:**`. The rule follows RST: a term followed *directly* by an
indented block is a definition (prose); a header, a blank line, then an
indented block is a literal block (code), and stays a code block. Bodies that
are code anyway -- decorators, doctests, column-aligned diagrams -- are left
alone too.

Run standalone or under pytest:
    uv run tests/test_api_docstring_custom_sections.py
    uv run pytest tests/test_api_docstring_custom_sections.py
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.parser.docstring import parse_docstring, render_custom_sections  # noqa: E402


def _doc(text: str) -> str:
    return textwrap.dedent(text).strip("\n")


# The docstring Copilot flagged, trimmed to its shape.
CLUSTERED = _doc('''
    A TaskEnvironment that emits a Kubernetes JobSet.

    Args:
        replicas: Number of pods. Required.

    Restarts and retries:
        A clustered task can start over at three levels.

        1. **JobSet restarts** (`failure_policy`): when a worker fails, every pod is
           recreated in place.
        2. **User retries** (`retries=` on `env.task`): a new attempt.

        The failure message names the cause.

    Waiting and queues:
        The task waits until every worker is ready. Bound the wait with
        `timeout=flyte.Timeout(max_queued_time=..., deadline=...)` on `env.task`: `max_queued_time`
        covers each attempt.

        ```python
        env = ClusteredTaskEnvironment(
            ...,
        )
        ```
''')


def test_custom_section_body_is_dedented_under_a_bold_lead_in():
    out = render_custom_sections(CLUSTERED)
    assert "**Restarts and retries:**\n\nA clustered task can start over" in out
    assert "\n1. **JobSet restarts**" in out
    # List-item continuation keeps its offset relative to the marker.
    assert "\n   recreated in place." in out
    assert "**Waiting and queues:**\n\nThe task waits" in out


def test_a_fence_inside_a_custom_section_lands_at_column_zero():
    out = render_custom_sections(CLUSTERED)
    assert "\n```python\nenv = ClusteredTaskEnvironment(\n    ...,\n)\n```" in out


def test_known_sections_are_still_parsed():
    info = parse_docstring(CLUSTERED, source=None)
    assert info["params"]["replicas"]["doc"] == "Number of pods. Required."
    assert "**Restarts and retries:**" in info["docstring"]
    assert "    A clustered task" not in info["docstring"]


def test_no_line_of_a_custom_section_is_left_as_an_indented_code_block():
    info = parse_docstring(CLUSTERED, source=None)
    lines = info["docstring"].split("\n")
    fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence:
            assert not line.startswith("    "), line


def test_name_description_body_becomes_a_list():
    doc = _doc('''
        A batcher.

        Type Parameters:
            RecordT: The input record type.
            ResultT: The per-record output type.
    ''')
    out = render_custom_sections(doc)
    assert "**Type Parameters:**\n\n- `RecordT`: The input record type.\n- `ResultT`: The per-record output type." in out


def test_single_line_prose_body():
    doc = _doc('''
        List all API keys.

        Yields:
            ApiKey instances
    ''')
    assert render_custom_sections(doc) == "List all API keys.\n\n**Yields:**\n\nApiKey instances"


# --- Left alone: these bodies are code ---------------------------------------


def test_rst_literal_block_is_left_alone():
    doc = _doc('''
        Module summary.

        Typical use::

            from flyte.remote import Task
    ''')
    assert render_custom_sections(doc) == doc


def test_header_then_blank_line_then_code_is_left_alone():
    doc = _doc('''
        Module summary.

        Basic usage:

            import flyte
            flyte.init()
    ''')
    assert render_custom_sections(doc) == doc


def test_adjacent_body_with_decorators_is_left_alone():
    doc = _doc('''
        Decorator.

        Decorator Order:
            For tasks, @wandb_init must be the outermost decorator:
            @wandb_init
            @env.task
            async def my_task():
                ...
    ''')
    assert render_custom_sections(doc) == doc


def test_column_aligned_diagram_is_left_alone():
    doc = _doc('''
        Transformer.

        Life Cycle:
            python val -> msgpack bytes -> binary literal scalar -> python val
                          (to_literal)                             (from_binary_idl)
    ''')
    assert render_custom_sections(doc) == doc


def test_doctest_body_is_left_alone():
    doc = _doc('''
        Adds.

        Usage:
            >>> add(1, 2)
            3
    ''')
    assert render_custom_sections(doc) == doc


def test_code_that_parses_is_left_alone():
    doc = _doc('''
        Summary.

        Usage:
            env = flyte.TaskEnvironment(name="x")
    ''')
    assert render_custom_sections(doc) == doc


def test_a_header_inside_a_fence_is_left_alone():
    doc = _doc('''
        Summary.

        ```python
        def foo():
            return 1
        ```
    ''')
    assert render_custom_sections(doc) == doc


def test_the_summary_line_is_never_a_header():
    """It feeds the one-line description in the module's function table."""
    doc = _doc('''
        Helper function, used to extract out the following information:
          - base type
          - column information
    ''')
    assert render_custom_sections(doc) == doc


def test_text_after_a_sphinx_field_is_left_alone():
    """The parser folds it into the field's description, a table cell."""
    doc = _doc('''
        Get the parameter.

        :param param_name: The name of the parameter.

        For example:
            exception: TypeError, 'a' has no type.
    ''')
    assert render_custom_sections(doc) == doc


def test_a_header_glued_to_the_paragraph_above_is_left_alone():
    doc = _doc('''
        For untyped dict, dataclass, and pydantic basemodel:
        Life Cycle:
            python val -> msgpack bytes -> python val
    ''')
    assert render_custom_sections(doc) == doc


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
