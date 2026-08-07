from typing import Optional
import re

def docstring_summary(docstring: Optional[str]) -> str:
    if docstring is None:
        return ""

    docstring = str(docstring).strip()

    # Skip alert/admonition lines at the start (GitHub alerts and MkDocs admonitions)
    lines = docstring.split("\n")
    content_lines = []
    in_alert = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">") or stripped.startswith("!!!"):
            in_alert = True
            continue
        if in_alert and stripped == "":
            in_alert = False
            continue
        if not in_alert:
            content_lines.append(line)

    docstring = "\n".join(content_lines).strip()
    if not docstring:
        return ""

    def replace_shortcode_periods(match):
        return match.group(0).replace(".", "___PERIOD___")

    # Periods that do not end a sentence: Hugo shortcodes, inline code spans and
    # any leftover RST role. Splitting inside one of these cuts the summary
    # mid-token, e.g. `Wrap a ``agents.function_tool`` ...` -> "Wrap a ``agents."
    # Ordered longest-delimiter-first so ``x`` is not matched as `` + x + ``.
    pattern = (
        r"{{<.*?>}}"          # Hugo shortcode
        r"|:[a-z]+:`[^`]*`"   # RST role, e.g. :class:`~pkg.mod.Name`
        r"|``[^`]+``"         # double-backtick code span
        r"|`[^`]+`"           # single-backtick code span
    )
    protected_docstring = re.sub(pattern, replace_shortcode_periods, docstring, flags=re.DOTALL)
    first_sentence = protected_docstring.split(".")[0]
    first_sentence = first_sentence.replace("___PERIOD___", ".")
    first_line = first_sentence.split("\n")[0].strip()
    return first_line + "." if first_line else ""


def test_docstring_summary():
    # A dotted name inside a code span is not a sentence boundary.
    assert docstring_summary(
        "Flyte-aware replacement for ``agents.function_tool`` — named ``tool``."
    ) == "Flyte-aware replacement for ``agents.function_tool`` — named ``tool``."
    assert docstring_summary(
        "Default implementation runs `ainvoke` in parallel using `asyncio.gather`."
    ) == "Default implementation runs `ainvoke` in parallel using `asyncio.gather`."
    # Leftover RST roles are protected too.
    assert docstring_summary(
        "A :class:`flyte.report.Timeline` that defaults to the ``Agent`` tab."
    ) == "A :class:`flyte.report.Timeline` that defaults to the ``Agent`` tab."
    # Only the first sentence is kept, and the rest of the body is dropped.
    assert docstring_summary("Do a thing. And then another.") == "Do a thing."
    assert docstring_summary("Summary line.\n\nMore detail here.") == "Summary line."
    # Leading admonitions are still skipped.
    assert docstring_summary('!!! note "X"\n\nActual summary.') == "Actual summary."
    assert docstring_summary(None) == ""
    print("test_docstring_summary: ok")


if __name__ == "__main__":
    test_docstring_summary()
