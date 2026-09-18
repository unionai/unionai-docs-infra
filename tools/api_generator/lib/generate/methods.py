import io
import re
from typing import List

from lib.ptypes import MethodInfo, ParamInfo
from lib.generate.docstring import docstring_summary
from lib.generate.helper import generate_anchor_from_name


# An inline code span: a run of N backticks closed by a run of exactly N, on
# one line. Its contents are code, so they must survive verbatim. Escaping a
# `>` inside a span does not round-trip -- a markdown renderer leaves entities
# alone inside code, so `->` written as `-&gt;` reaches the reader as literal
# `-&gt;` (DOC-1323). Outside code the entity decodes back to `>`, so prose
# escaping stays as it was.
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)")


def _escape_outside_inline_code(line):
    """Escape `<`/`>` in one line, leaving inline code spans untouched."""
    result = []
    pos = 0
    for match in _INLINE_CODE_RE.finditer(line):
        chunk = line[pos:match.start()]
        result.append(chunk.replace("<", "&lt;").replace(">", "&gt;"))
        result.append(match.group(0))
        pos = match.end()
    tail = line[pos:]
    result.append(tail.replace("<", "&lt;").replace(">", "&gt;"))
    return ''.join(result)


def escape_html_preserve_code_blocks(text):
    """Escape HTML characters in text while preserving code blocks and blockquotes."""
    if not text:
        return text

    # Split on code block delimiters (```)
    parts = re.split(r'(```.*?```)', text, flags=re.DOTALL)

    result = []
    for i, part in enumerate(parts):
        # Even indices are regular text, odd indices are code blocks
        if i % 2 == 0:  # Regular text - escape HTML but preserve blockquotes
            lines = part.split('\n')
            escaped_lines = []
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith('>'):
                    # Preserve blockquote prefix, escape only the content after it
                    prefix_len = len(line) - len(stripped)
                    prefix = line[:prefix_len]
                    # Find the blockquote marker(s) and content
                    bq_match = re.match(r'^(>+\s*)', stripped)
                    if bq_match:
                        bq_prefix = bq_match.group(1)
                        content = stripped[len(bq_prefix):]
                        content = _escape_outside_inline_code(content)
                        escaped_lines.append(f"{prefix}{bq_prefix}{content}")
                    else:
                        escaped_lines.append(_escape_outside_inline_code(line))
                else:
                    escaped_lines.append(_escape_outside_inline_code(line))
            result.append('\n'.join(escaped_lines))
        else:  # Code block - don't escape
            result.append(part)

    return ''.join(result)


# inspect's kinds for `*args` / `**kwargs`.
_VARARG_PREFIX = {"VAR_POSITIONAL": "*", "VAR_KEYWORD": "**"}


def param_display_name(param: ParamInfo) -> str:
    """`*args` / `**kwargs` -- the stars belong to the name, not the type."""
    kind = param.get("kind")
    if kind in _VARARG_PREFIX:
        return f"{_VARARG_PREFIX[kind]}{param['name']}"
    if kind is None:
        # A param with no kind was synthesized rather than introspected; fall
        # back to the conventional names.
        if param["name"] == "kwargs":
            return "**kwargs"
        if param["name"] == "args":
            return "*args"
    return param["name"]


def generate_method_decl(
    name: str,
    method: MethodInfo,
    output: io.TextIOWrapper,
    is_class: bool = False,
    is_protocol: bool = False,
):
    # Filter out 'self' parameter
    filtered_params = [param for param in method["params"] if param["name"] != "self"]

    if method["framework"] == "syncify":
        qual_name = f"{method['parent_name']}.{name}" if method["parent_name"] else name
        output.write(
            f"""
> [!NOTE] This method can be called both synchronously or asynchronously.
> Default invocation is sync and will block.
> To call it asynchronously, use the function `.aio()` on the method name itself, e.g.,:
> `result = await {qual_name}.aio()`.
"""
        )

    output.write("```python\n")
    try:
        if len(filtered_params) == 0:
            output.write(f"def {name}()\n")
            return

        if is_protocol:
            output.write(f"protocol {name}()\n")
        elif is_class:
            output.write(f"class {name}(\n")
        else:
            output.write(f"def {name}(\n")

        if not is_protocol:
            for param in filtered_params:
                output.write(f"    {param_display_name(param)}")
                if "type" in param and param["type"]:
                    output.write(
                        f": {format_type(param['type'], code=True)}"
                    )
                # A dropped default reads as "required" (DOC-1383). The parser
                # already carries it; only `None` means there was none.
                if param.get("default") is not None:
                    output.write(f" = {param['default']}")
                output.write(",\n")

            if not is_class and method["return_type"] and method["return_type"] != "None":
                output.write(
                    f") -> {format_type(method['return_type'], markdown=False)}\n"
                )
            else:
                output.write(")\n")
    finally:
        output.write("```\n")


def format_type(
    type: str | None, code=False, escape_or=False, markdown=True
) -> str:
    output = ""
    if type and type.startswith("<class '") and type.endswith("'>"):
        output = type[8:-2]
    else:
        output = type if type != "" else ""

    if output == "" or output is None:
        return ""

    if escape_or:
        output = output.replace("|", "\\|")

    if markdown:
        return f"`{output}`" if not code else str(output)
    else:
        return f"{output}" if not code else str(output)


def generate_params(method: MethodInfo, output: io.TextIOWrapper):
    # Filter out 'self' parameter
    filtered_params = [param for param in method["params"] if param["name"] != "self"]

    # Check if there are any parameters left after filtering
    if not filtered_params:
        # output.write("No parameters\n")
        return

    output.write("| Parameter | Type | Description |\n")
    output.write("|-|-|-|\n")
    for param in filtered_params:
        typeOutput = format_type(
            param["type"] if "type" in param else "", escape_or=True
        )
        display_name = param_display_name(param)

        # Look for documentation in params_doc field first, then fallback to param doc
        doc = ""
        if "params_doc" in method and method["params_doc"] and param["name"] in method["params_doc"]:
            doc = method["params_doc"][param["name"]]["doc"] or ""
        elif "doc" in param:
            doc = param["doc"] or ""

        # Clean up the doc string - replace newlines with spaces and escape markdown table characters and HTML
        if doc:
            # First escape HTML while preserving code blocks, then clean up for table format
            doc = escape_html_preserve_code_blocks(doc)
            doc = doc.replace("\n", " ").replace("|", "\\|").strip()

            # Remove redundant type information from the beginning of descriptions
            # Pattern: "(type) description..." where type matches what's already in the Type column
            doc = re.sub(r'^\([^)]+\)\s*', '', doc)
            output.write(f"| `{display_name}` | {typeOutput} | {doc} |\n")
        else:
            output.write(f"| `{display_name}` | {typeOutput} | |\n")
    output.write("\n")


def generate_signature(method: MethodInfo):
    params = []
    for param in method["params"]:
        param_str = param["name"]
        if "type" in param and param["type"]:
            param_str += f": {param['type']}"
        params.append(param_str)

    return f"{method['name']}({', '.join(params)}) -> {method['return_type']}"


def generate_signature_simple(method: MethodInfo, name: str = ""):
    result = "".join(
        [
            name if name else method["name"],
            "(",
            ", ".join([param["name"] for param in method["params"]]),
            ")",
            " -> ",
            method["return_type"],
        ]
    )
    return result


def generate_method_list(
    methods: List[MethodInfo], output: io.TextIOWrapper, doc_level: int
):
    output.write(f"{'#' * (doc_level)} Methods\n\n")

    output.write("| Method | Description |\n")
    output.write("|-|-|\n")

    for method in methods:
        output.write(
            f"| [`{method['name']}()`]({generate_method_link(method['name'])}) | {docstring_summary(method['doc'])} |\n"
        )

    output.write("\n\n")


def generate_method_link(name: str) -> str:
    anchor = generate_anchor_from_name(name)
    return f"#{anchor}"


def generate_return_doc(info, output: io.TextIOWrapper):
    """Render return documentation. Works with MethodInfo or any dict with return_doc."""
    return_doc = info.get("return_doc")
    if not return_doc:
        return
    return_doc = escape_html_preserve_code_blocks(return_doc)
    if "\n" in return_doc:
        output.write(f"**Returns**\n\n{return_doc}\n\n")
    else:
        output.write(f"**Returns:** {return_doc}\n\n")


def generate_raises(info, output: io.TextIOWrapper):
    """Render raises documentation. Works with MethodInfo, ClassDetails, or any dict with raises."""
    raises = info.get("raises")
    if not raises:
        return
    output.write("**Raises**\n\n")
    output.write("| Exception | Description |\n")
    output.write("|-|-|\n")
    for entry in raises:
        exc = f"`{entry['exception']}`" if entry.get("exception") else ""
        doc = (entry.get("doc") or "").replace("\n", " ").replace("|", "\\|").strip()
        doc = escape_html_preserve_code_blocks(doc)
        output.write(f"| {exc} | {doc} |\n")
    output.write("\n")


def generate_notes(info, output: io.TextIOWrapper):
    """Render notes as a Hugo notice block. Works with MethodInfo, ClassDetails, or any dict with notes."""
    notes = info.get("notes")
    if not notes:
        return
    output.write("> [!NOTE]\n")
    for line in notes.strip().split("\n"):
        if line.strip():
            output.write(f"> {line}\n")
        else:
            output.write(">\n")
    output.write("\n")


def generate_examples(info, output: io.TextIOWrapper):
    """Render Example/Examples sections. Wraps code content in fenced code blocks."""
    examples = info.get("examples")
    if not examples:
        return
    text = examples.strip()
    output.write("**Example:**\n\n")
    if "```" in text:
        # Already has fenced code blocks — write as-is
        output.write(text + "\n\n")
    elif ">>>" in text:
        # Doctest markers — wrap in fenced block
        output.write("```python\n")
        output.write(text + "\n")
        output.write("```\n\n")
    elif all(line == "" or line.startswith("    ") or line.startswith("\t") for line in text.split("\n")):
        # Indented code block (from RST Example::) — dedent and fence
        import textwrap
        output.write("```python\n")
        output.write(textwrap.dedent(text) + "\n")
        output.write("```\n\n")
    else:
        # Regular text example
        output.write(text + "\n\n")


def generate_method(method: MethodInfo, output: io.TextIOWrapper, doc_level: int):
    output.write(f"{'#' * (doc_level+1)} {method['name']}()\n\n")
    generate_method_decl(method["name"], method, output)
    if method["doc"]:
        # Escape HTML characters in method documentation while preserving code blocks
        doc = escape_html_preserve_code_blocks(method["doc"])
        output.write(f"{doc}\n\n")
    generate_params(method, output)
    generate_return_doc(method, output)
    generate_raises(method, output)
    generate_notes(method, output)
    generate_examples(method, output)
