from typing import List, Optional
import inspect
import re

# Periods that do not end a sentence: Hugo shortcodes, inline code spans and
# any leftover RST role. Splitting inside one of these cuts the summary
# mid-token, e.g. `Wrap a ``agents.function_tool`` ...` -> "Wrap a ``agents."
# Ordered longest-delimiter-first so ``x`` is not matched as `` + x + ``.
PROTECTED_SPAN = (
    r"{{<.*?>}}"          # Hugo shortcode
    r"|:[a-z]+:`[^`]*`"   # RST role, e.g. :class:`~pkg.mod.Name`
    r"|``[^`]+``"         # double-backtick code span
    r"|`[^`]+`"           # single-backtick code span
)

_PERIOD_TOKEN = "___PERIOD___"

# A trailing period on one of these is an abbreviation, not a sentence end, even
# when the next word is capitalised ("... e.g. Union" is one sentence).
ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "cf", "al", "approx", "resp", "no",
    "dr", "mr", "mrs", "ms", "prof", "fig", "eq", "ref", "vol", "ca",
}

# What a new sentence may start with. Anything else after a period means the
# period was internal (a version number, an ellipsis inside prose, a filename).
_SENTENCE_START = "\"'`([*_"

# A first paragraph with no terminal punctuation at all is a shapeless run of
# prose. Up to this length it is a usable label ("Artifacts module"); past it we
# have no way to end it without inventing a boundary, so we emit nothing.
MAX_UNPUNCTUATED = 200


def _protect(text: str) -> str:
    return re.sub(
        PROTECTED_SPAN,
        lambda m: m.group(0).replace(".", _PERIOD_TOKEN),
        text,
        flags=re.DOTALL,
    )


def _unprotect(text: str) -> str:
    return text.replace(_PERIOD_TOKEN, ".")


def _is_underline(line: str) -> bool:
    """An RST section underline (`=====`, `-----`) rather than prose."""
    s = line.strip()
    return len(s) >= 3 and set(s) <= set("=-~^*+#`\"'")


def _strip_leading_noise(lines: List[str]) -> List[str]:
    """Drop everything before the first line of actual prose.

    A module docstring routinely opens with something that is a title rather
    than a description -- `## IO data types`, `# Syncify Module`, a GitHub alert,
    an MkDocs admonition, a code fence, or an RST underlined heading. Taking any
    of those as the page description restates the page title and tells the
    reader nothing, which is the defect this whole change exists to remove.
    """
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("#"):                      # markdown heading
            i += 1
            continue
        if stripped.startswith(">") or stripped.startswith("!!!"):  # alert / admonition
            i += 1
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):  # fenced code
            fence = stripped[:3]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                i += 1
            i += 1
            continue
        if i + 1 < len(lines) and _is_underline(lines[i + 1]):       # RST heading
            i += 2
            continue
        break
    return lines[i:]


def _first_paragraph(text: str) -> str:
    """The opening block of prose, as ONE line.

    A sentence cannot cross a blank line, so the paragraph is a safe bound; and
    joining its lines is what stops the extraction at a hard-wrapped line break.
    `flyte.io` is the case that proves it: its opening sentence wraps after
    "... in python to", so anything line-oriented emits that fragment.
    """
    lines = _strip_leading_noise(text.split("\n"))
    para: List[str] = []
    for line in lines:
        if not line.strip():
            break
        para.append(line.strip())
    return re.sub(r"\s+", " ", " ".join(para)).strip()


def _first_sentence(paragraph: str) -> Optional[str]:
    """The first sentence of a single-line paragraph, or None if unbounded.

    A period ends a sentence only when what follows it is whitespace (or the end
    of the text) AND what follows that could start a sentence. Both halves are
    load-bearing on real docstrings:

      * `flyte.ai - AI utilities for Flyte.`   the period in `flyte.ai` is
        followed by a letter, not whitespace -> not a boundary. Splitting on
        every period yields "flyte." for four modules.
      * `Task Notifications API for Flyte 2.0` same shape in a version number.
      * `Agent protocol for the flyte.ai.agents module.` only the last period
        qualifies.
    """
    protected = _protect(paragraph)
    for m in re.finditer(r"[.!?](['\")\]]*)(\s+|$)", protected):
        head = protected[: m.end(1)]
        tail = protected[m.end():]
        word = re.split(r"[\s(\[]", _unprotect(protected[: m.start()]))[-1]
        if word.lower().strip("(['\"") in ABBREVIATIONS:
            continue
        if not tail:
            return _unprotect(head)
        if tail[0].isupper() or tail[0].isdigit() or tail[0] in _SENTENCE_START:
            return _unprotect(head)
    return None


def docstring_description(docstring: Optional[str]) -> Optional[str]:
    """One-sentence page description for frontmatter, or None.

    None -- not "" -- is the honest output for a symbol with no docstring: the
    caller omits the key entirely rather than writing an empty or invented one.
    A wrong descriptor is worse than no descriptor, because it renders on the
    parent page of every reader passing through.
    """
    if not docstring:
        return None

    paragraph = _first_paragraph(inspect.cleandoc(str(docstring)))
    if not paragraph:
        return None

    sentence = _first_sentence(paragraph)
    if sentence is None:
        # No terminal punctuation anywhere in the opening paragraph.
        if len(paragraph) > MAX_UNPUNCTUATED:
            return None
        # A trailing colon or semicolon introduces a list or code block that is
        # not coming with us; "... two-step pattern:." reads as a typo.
        sentence = paragraph.rstrip(":;, ") + "."

    sentence = sentence.strip()
    return sentence or None


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

    protected_docstring = re.sub(PROTECTED_SPAN, replace_shortcode_periods, docstring, flags=re.DOTALL)
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
