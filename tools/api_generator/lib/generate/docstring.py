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

# An unpunctuated statement this long is not a statement, it is prose someone
# forgot to punctuate. Under it we have a usable label ("Artifacts module");
# over it we have no way to end it without inventing a boundary, so we emit
# nothing rather than cut it somewhere arbitrary.
MAX_UNPUNCTUATED = 200

# A bullet or numbered item. A docstring that opens on one has no prose summary
# at all (`flyteplugins.mlflow` and `flyteplugins.wandb` both open on a
# "Key features" list), and one item of a list is not a description of the whole.
LIST_ITEM = re.compile(r"^([-*+]\s|\d+[.)]\s)")


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


def _paragraph_lines(text: str) -> List[str]:
    """The lines of the opening block of prose, still separate.

    A sentence cannot cross a blank line, so the paragraph is a safe bound. The
    lines are kept apart because both units matter: a sentence is found in the
    whole paragraph, but when the paragraph holds no sentence at all the line
    breaks are the only structure left and have to be read.
    """
    lines = _strip_leading_noise(text.split("\n"))
    para: List[str] = []
    for line in lines:
        if not line.strip():
            break
        para.append(line.strip())
    return para


def _collapse(lines: List[str]) -> str:
    """Join lines into one, which is what defeats a hard-wrapped sentence.

    `flyte.io` is the case that proves it: its opening sentence wraps after
    "... in python to", so anything line-oriented emits that fragment.
    """
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


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

    lines = _paragraph_lines(inspect.cleandoc(str(docstring)))
    paragraph = _collapse(lines)
    if not paragraph:
        return None

    sentence = _first_sentence(paragraph)
    if sentence is None:
        # No sentence boundary anywhere in the opening paragraph, so the line
        # breaks are the only structure left. Follow them: keep joining while
        # the next line CONTINUES the statement, and stop where a new one
        # starts. A new statement opens with a capital or a list marker; a
        # continuation opens with anything else.
        #
        # All three shapes are real docstrings:
        #   `BigQueryTask.pre`        one statement, hard-wrapped --
        #     "This is the preexecute function that will be" / "called before
        #     the task is executed". Only the join reads as English.
        #   `ActionID.unique_id_str`  a lead-in and its template --
        #     "... in the format:" / "{project}-{domain}-{run_name}-...".
        #     Stopping at the colon promises a format and never gives it.
        #   `Image.with_pip_packages` two statements stacked --
        #     "... on top of the current image" / "Cannot be used in
        #     conjunction with conda". Joining these makes a run-on with a
        #     missing period in the middle, which reads worse than either line.
        #
        # This cannot re-break a hard-wrapped SENTENCE: a sentence has a period
        # and is resolved above, never here.
        candidate_lines = [lines[0]]
        for line in lines[1:]:
            if line[:1].isupper() or LIST_ITEM.match(line):
                break
            candidate_lines.append(line)
        candidate = _collapse(candidate_lines)
        if LIST_ITEM.match(candidate) or len(candidate) > MAX_UNPUNCTUATED:
            return None
        # A trailing colon or semicolon introduces a list or code block that is
        # not coming with us; "... two-step pattern:." reads as a typo.
        sentence = candidate.rstrip(":;, ") + "."

    sentence = sentence.strip()
    return sentence or None


def docstring_summary(docstring: Optional[str]) -> str:
    r"""One-line summary for a markdown TABLE CELL.

    Same extraction as `docstring_description` -- deliberately, because the two
    used to disagree about the same docstring. The frontmatter said "flyte.ai --
    AI utilities for Flyte." while the Directory table on the parent page said
    "flyte."; the frontmatter said the whole of `flyte.io`'s opening sentence
    while the table said "## IO data types.". One symbol, two extractors, two
    answers, and the reader sees both on the way in.

    A cell has one constraint frontmatter does not: a literal `|` ends it, so it
    is escaped (GFM honours `\|` inside code spans too). Length is NOT capped --
    a table cell wraps, and cutting a sentence to fit a column is the exact
    defect this function was rewritten to remove. The widest cell in the current
    corpus is 188 characters, against a mean of 62.

    Returns "" -- never None -- when there is nothing to say, so the row still
    renders with an empty description rather than going missing.
    """
    summary = docstring_description(docstring)
    if not summary:
        return ""
    return summary.replace("|", "\\|")


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
    # The cells and the frontmatter now derive from one extractor, so the shapes
    # that used to break a cell no longer can.
    assert docstring_summary("flyte.ai — AI utilities for Flyte.") == \
        "flyte.ai — AI utilities for Flyte."
    assert docstring_summary("## IO data types\n\nA sentence that wraps\nacross two lines.") == \
        "A sentence that wraps across two lines."
    # A literal pipe would end the cell.
    assert docstring_summary("Accepts `a | b` unions.") == "Accepts `a \\| b` unions."
    print("test_docstring_summary: ok")


if __name__ == "__main__":
    test_docstring_summary()
