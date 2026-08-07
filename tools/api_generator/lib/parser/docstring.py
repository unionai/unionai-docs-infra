import inspect
import json
import re
from typing import TypedDict, Optional

from lib.ptypes import ParamDict, ParamInfo


# Google-style section headers → internal section names
_GOOGLE_SECTIONS = {
    "Args:": "args",
    "Arguments:": "args",
    "Attributes:": "args",
    "Returns:": "returns",
    "Return:": "returns",
    "Raises:": "raises",
    "Note:": "notes",
    "Notes:": "notes",
    "Example:": "examples",
    "Examples:": "examples",
}

# Sphinx return-like directives
_SPHINX_RETURNS = ["return", "rcode", "result", "rtype"]

# A Google section header sitting alone on its line, at any indentation.
_SECTION_HEADER_RE = re.compile(
    r"^(?P<indent> *)(?:%s) *$" % "|".join(re.escape(k) for k in _GOOGLE_SECTIONS)
)

# Sphinx cross-reference roles, e.g. :class:`~pkg.mod.Name` or :func:`name`.
_RST_ROLE_RE = re.compile(
    r":(?:py:)?(?:class|func|meth|mod|attr|exc|obj|data|const|term|ref|doc):`([^`]+)`"
)

# An explicit RST role title: :class:`Some Title <pkg.mod.Name>`
_RST_EXPLICIT_TITLE_RE = re.compile(r"^(?P<title>.*?)\s*<(?P<target>[^>]+)>$")

# A MkDocs admonition marker: !!! type "optional title". The title is usually
# quoted but not always, so take the rest of the line and unquote it.
_ADMONITION_RE = re.compile(r"^ *!!!\s+(?P<kind>[\w-]+)\s*(?P<title>.*?)\s*$")

# MkDocs admonition type -> the GitHub-style alert the docs theme renders.
_ADMONITION_ALERTS = {
    "warning": "WARNING",
    "caution": "WARNING",
    "danger": "WARNING",
    "deprecated": "WARNING",
    "failure": "WARNING",
    "note": "NOTE",
    "info": "NOTE",
    "version-added": "NOTE",
    "version-changed": "NOTE",
    "abstract": "TIP",
    "tip": "TIP",
    "hint": "TIP",
    "success": "TIP",
}

# Types whose body is normally a fenced code block. A blockquote would swallow
# the fence, so these get a bold lead-in and keep the body as real Markdown.
_ADMONITION_LEADINS = {
    "example": "Example",
    "quote": "Quote",
}


def dedent_nested_sections(docstring: str) -> str:
    """
    Re-align a docstring whose body sits one indentation level below its summary.

    ``functools.wraps``-style wrappers build a docstring by prepending a summary
    line to another function's ``__doc__``. The borrowed body keeps its original
    source indentation on every line but the first, and ``inspect.cleandoc``
    cannot strip it because that first line already sits at column 0. The Google
    section headers then never appear at base indentation, so ``Args:`` and
    ``Returns:`` are never parsed: the parameter table is emitted with empty
    descriptions and the prose renders as an indented code block.

    When every Google section header is indented, shift the indented block back
    to the left by that amount. A docstring whose headers already sit at base
    indentation is returned unchanged.
    """
    lines = docstring.split("\n")
    indents = [
        len(m.group("indent"))
        for m in (_SECTION_HEADER_RE.match(line) for line in lines)
        if m
    ]
    if not indents:
        return docstring

    shift = min(indents)
    if shift <= 0:
        return docstring

    prefix = " " * shift
    return "\n".join(
        line[shift:] if line.startswith(prefix) else line for line in lines
    )


def convert_rst_roles(text: str) -> str:
    """
    Turn Sphinx cross-reference roles into plain Markdown code spans.

    Docstrings written for Sphinx reference other objects with roles such as
    ``:class:`~pkg.mod.Name```. Nothing downstream understands them, so they
    reach the site verbatim and render as the literal text ``:class:`Name```.
    A code span is the right target: the docs site autolinks backticked
    identifiers to their API reference using the generated linkmaps.

    RST display semantics are preserved -- a leading ``~`` means "show only the
    last dotted component", and an explicit ``Title <target>`` wins over the
    target. Code fences are left alone.
    """
    def replace(match: re.Match) -> str:
        target = match.group(1).strip()

        explicit = _RST_EXPLICIT_TITLE_RE.match(target)
        if explicit:
            return f"`{explicit.group('title').strip()}`"

        if target.startswith("~"):
            target = target.lstrip("~").split(".")[-1]
        return f"`{target}`"

    out = []
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
        elif in_fence:
            out.append(line)
        else:
            out.append(_RST_ROLE_RE.sub(replace, line))
    return "\n".join(out)


class DocstringInfo(TypedDict):
    docstring: str
    params: ParamDict
    return_doc: Optional[str]
    raises: Optional[list]
    notes: Optional[str]
    examples: Optional[str]


def parse_docstring(docstring: str | None, source) -> Optional[DocstringInfo]:
    if not docstring:
        return None

    if "See help(type(self)) for accurate signature." in docstring:
        return None

    try:
        method_decl = f"{source.__name__}{inspect.signature(source)}"
        if method_decl.startswith(docstring):
            return None
    except:
        pass

    # Normalize indentation: handles docstrings where the first line is on
    # the same line as """ (no indent) but continuation lines are indented.
    docstring = inspect.cleandoc(docstring)

    # Pull a wrapper-borrowed body back to the base indentation so its
    # Args:/Returns: sections are visible to the section parser below.
    docstring = dedent_nested_sections(docstring)

    # Convert RST-style "Example::" to Google-style "Example:" so the
    # section parser picks it up. Also handles "Examples::".
    docstring = re.sub(r'^(\s*Examples?)::', r'\1:', docstring, flags=re.MULTILINE)

    # Turn Sphinx cross-reference roles into Markdown code spans. Runs before
    # the admonition pass, while code fences are still at the line start.
    docstring = convert_rst_roles(docstring)

    # Removes the special !!!! notes
    docstring = format_three_exclamation_notes(docstring)

    lines = docstring.split("\n")

    result = DocstringInfo(
        docstring="", params={}, return_doc=None, raises=None, notes=None, examples=None
    )

    sphinx_param = None     # Current Sphinx :param: being accumulated
    section = None          # Active Google section: "args", "returns", "raises", "notes"
    leading_spaces = -1     # Docstring base indentation
    section_indent = -1     # Content indentation within a Google section
    cont_indent = -1        # Further indentation for continuation lines
    args_param = None       # Current Args parameter name
    raises_entry = None     # Current Raises entry dict

    for line in lines:
        # Determine base indentation from first non-empty line
        if leading_spaces == -1:
            if line.strip() == "":
                continue
            leading_spaces = len(line) - len(line.lstrip())

        line = line[leading_spaces:]
        stripped = line.strip()
        at_base = bool(stripped) and not line[0:1].isspace()

        # Non-empty line at base indentation exits any active Google section
        if section is not None and at_base:
            section = None
            section_indent = -1
            cont_indent = -1
            args_param = None
            raises_entry = None

        # Check for Google-style section header
        if at_base and stripped in _GOOGLE_SECTIONS:
            section = _GOOGLE_SECTIONS[stripped]
            section_indent = -1
            cont_indent = -1
            args_param = None
            raises_entry = None
            sphinx_param = None
            continue

        # --- Inside a Google section ---

        if section == "args":
            if not stripped:
                continue
            if section_indent == -1:
                section_indent = len(line) - len(line.lstrip())
            content = line[section_indent:]

            if not content[0:1].isspace():
                # New parameter: "name: desc" or "name (type): desc"
                colon = content.find(":")
                if colon < 0:
                    continue
                name_part = content[:colon].strip()
                desc_part = content[colon + 1:].strip()

                param_type = None
                if "(" in name_part and name_part.endswith(")"):
                    paren = name_part.rfind("(")
                    args_param = name_part[:paren].strip()
                    param_type = name_part[paren + 1:-1].strip()
                else:
                    args_param = name_part

                result["params"][args_param] = ParamInfo(
                    name=args_param, doc=desc_part, type=param_type,
                    default=None, kind=None,
                )
                cont_indent = -1
            elif args_param:
                # Continuation of previous param description
                if cont_indent == -1:
                    cont_indent = len(content) - len(content.lstrip())
                text = content[cont_indent:]
                p = result["params"][args_param]
                p["doc"] = (p["doc"] + "\n" + text) if p["doc"] else text
            continue

        if section == "returns":
            if section_indent == -1:
                if not stripped:
                    continue  # Skip leading blanks
                section_indent = len(line) - len(line.lstrip())
            content = line[section_indent:] if section_indent > 0 else line
            if result["return_doc"] is not None:
                result["return_doc"] += "\n" + content
            else:
                result["return_doc"] = content
            continue

        if section == "raises":
            if not stripped:
                continue
            if section_indent == -1:
                section_indent = len(line) - len(line.lstrip())
            content = line[section_indent:]

            if not content[0:1].isspace():
                # New entry: "ExceptionType: description"
                colon = content.find(":")
                if colon >= 0:
                    exc = content[:colon].strip()
                    doc = content[colon + 1:].strip()
                else:
                    exc = content.strip()
                    doc = ""
                raises_entry = {"exception": exc, "doc": doc}
                if result["raises"] is None:
                    result["raises"] = []
                result["raises"].append(raises_entry)
                cont_indent = -1
            elif raises_entry:
                if cont_indent == -1:
                    cont_indent = len(content) - len(content.lstrip())
                text = content[cont_indent:]
                raises_entry["doc"] = (
                    (raises_entry["doc"] + " " + text) if raises_entry["doc"] else text
                )
            continue

        if section == "notes":
            if section_indent == -1:
                if not stripped:
                    continue  # Skip leading blanks
                section_indent = len(line) - len(line.lstrip())
            content = line[section_indent:] if section_indent > 0 else line
            if result["notes"] is not None:
                result["notes"] += "\n" + content
            else:
                result["notes"] = content
            continue

        if section == "examples":
            if section_indent == -1:
                if not stripped:
                    continue  # Skip leading blanks
                section_indent = len(line) - len(line.lstrip())
            content = line[section_indent:] if section_indent > 0 else line
            if result["examples"] is not None:
                result["examples"] += "\n" + content
            else:
                result["examples"] = content
            continue

        # --- No Google section: Sphinx directives and regular text ---

        # Sphinx :return:/:rtype: etc.
        sphinx_return = False
        for r in _SPHINX_RETURNS:
            prefix = f":{r}:"
            if stripped.startswith(prefix):
                result["return_doc"] = stripped[len(prefix):].strip()
                sphinx_param = None
                sphinx_return = True
                break
        if sphinx_return:
            continue

        # Sphinx :raises ExcType: description
        if stripped.startswith(":raises"):
            rest = stripped[len(":raises"):].lstrip()
            if rest.startswith(":"):
                exc, doc = "", rest[1:].strip()
            else:
                colon = rest.find(":")
                if colon >= 0:
                    exc = rest[:colon].strip()
                    doc = rest[colon + 1:].strip()
                else:
                    exc, doc = rest.strip(), ""
            if result["raises"] is None:
                result["raises"] = []
            result["raises"].append({"exception": exc, "doc": doc})
            sphinx_param = None
            continue

        # Sphinx :param name: description
        if stripped.startswith(":param"):
            parts = stripped.split(":")
            name = parts[1].strip().replace("param ", "")
            doc = parts[2].strip() if len(parts) > 2 else ""
            sphinx_param = name
            result["params"][name] = ParamInfo(
                name=name, doc=doc, type=None, default=None, kind=None,
            )
            continue

        # Continuation of Sphinx :param:
        if sphinx_param:
            if not stripped.startswith(":"):
                p = result["params"][sphinx_param]
                p["doc"] = (p["doc"] + "\n" + stripped) if p["doc"] else stripped
                continue
            else:
                sphinx_param = None

        # Regular text → docstring
        result["docstring"] += line + "\n"

    return result


def test_parse_params():
    test = """
    Call this with any Encoder or Decoder to register it with the\
          flytekit type system. If your handler does not\nspecify a protocol (e.g.\
          s3, gs, etc.) field, then\n\n:param h: The StructuredDatasetEncoder or\
          StructuredDatasetDecoder you wish to register with this transformer.\n\
          :param default_for_type: If set, when a user returns from a task an instance\
          of the dataframe the handler\n  handles, e.g. ``return pd.DataFrame(...)``,\
          not wrapped around the ``StructuredDataset`` object, we will\n  use this\
          handler's protocol and format as the default, effectively saying that\
          this handler will be called.\n  Note that this shouldn't be set if your\
          handler's protocol is None, because that implies that your handler\n \
          is capable of handling all the different storage protocols that flytekit's\
          data persistence layer is aware of.\n  In these cases, the protocol is\
          determined by the raw output data prefix set in the active context.\n\
          :param override: Override any previous registrations. If default_for_type\
          is also set, this will also override\n  the default.\n:param default_format_for_type:\
          Unlike the default_for_type arg that will set this handler's format and\
          storage\n  as the default, this will only set the format. Error if already\
          set, unless override is specified.\n:param default_storage_for_type: Same\
          as above but only for the storage format. Error if already set,\n  unless\
          override is specified.
    """
    print(json.dumps(parse_docstring(test, source=None), indent=2))


def test_parse_args():
    doc_with_args = """
    This class is used to specify the docker image that will be used to run the task.

    Args:
        name: name of the image.
        python_version: python version of the image. Use default python in the base image if None.
        builder: Type of plugin to build the image. Use envd by default.
        source_root: source root of the image.
        env: environment variables of the image.
        registry: registry of the image.
        pip_secret_mounts: Specify a list of tuples to mount secret for pip install. Each tuple should contain the path to
            the secret file and the mount path. For example, [(".gitconfig", "/etc/gitconfig")]. This is experimental and
            the interface may change in the future. Configuring this should not change the built image.
        pip_extra_args: Specify one or more extra pip install arguments as a space-delimited string
        registry_config: Specify the path to a JSON registry config file
        source_copy_mode: This option allows the user to specify which source files to copy from the local host, into the image.
            Not setting this option means to use the default flytekit behavior. The default behavior is:
                - if fast register is used, source files are not copied into the image (because they're already copied
                  into the fast register tar layer).
                - if fast register is not used, then the LOADED_MODULES (aka 'auto') option is used to copy loaded
                  Python files into the image.

            If the option is set by the user, then that option is of course used.
        copy: List of files/directories to copy to /root. e.g. ["src/file1.txt", "src/file2.txt"]
        python_exec: Python executable to use for install packages
    """
    print(json.dumps(parse_docstring(doc_with_args, source=None), indent=2))


def test_parse_google_sections():
    doc = """
    Open a file for reading or writing.

    Args:
        mode: The mode to open the file in (default: 'rb').
        block_size: Size of blocks for reading in bytes.

    Returns:
        An async file-like object that can be used with async read/write operations.

    Raises:
        ValueError: If the mode is not supported.
        FileNotFoundError: If the file does not exist.

    Note:
        This method is async and should be awaited.
    """
    print(json.dumps(parse_docstring(doc, source=None), indent=2))


def convert_pydantic_links(text: str) -> str:
    """
    Convert pydantic documentation links to absolute URLs.

    Handles two formats:
    1. Relative path links: ../concepts/models.md#model-copy
    2. Reference-style links: [`text`][pydantic.BaseModel.something]
    """
    import re
    import sys

    def replace_inline_link(match):
        link_text = match.group(1)
        link_path = match.group(2)

        # Check if it's a relative pydantic docs link
        if link_path.startswith('../concepts/') or link_path.startswith('./concepts/'):
            # Convert ../concepts/models.md#model-copy to concepts/models/#model-copy
            path = link_path.lstrip('./')
            # Remove .md extension and adjust anchor
            path = re.sub(r'\.md(#|$)', r'/\1', path)
            return f'[{link_text}](https://docs.pydantic.dev/latest/{path})'

        return match.group(0)

    def replace_reference_link(match):
        link_text = match.group(1)
        reference = match.group(2)

        # Map pydantic references to their API doc pages
        if reference.startswith('pydantic_core.'):
            url = f'https://docs.pydantic.dev/latest/api/pydantic_core/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.main.BaseModel.') or reference.startswith('pydantic.BaseModel.'):
            url = f'https://docs.pydantic.dev/latest/api/base_model/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.config.ConfigDict.') or reference.startswith('pydantic.ConfigDict'):
            url = f'https://docs.pydantic.dev/latest/api/config/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.fields.'):
            url = f'https://docs.pydantic.dev/latest/api/fields/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.functional_serializers.'):
            url = f'https://docs.pydantic.dev/latest/api/functional_serializers/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.root_model.'):
            url = f'https://docs.pydantic.dev/latest/api/root_model/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.types.'):
            url = f'https://docs.pydantic.dev/latest/api/types/#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('pydantic.'):
            # Generic pydantic reference - try base_model as default
            url = f'https://docs.pydantic.dev/latest/api/base_model/#{reference}'
            return f'[{link_text}]({url})'

        # Map Python stdlib references to docs.python.org
        elif reference.startswith('object.'):
            url = f'https://docs.python.org/3/library/stdtypes.html#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('functools.'):
            url = f'https://docs.python.org/3/library/functools.html#{reference}'
            return f'[{link_text}]({url})'
        elif reference.startswith('inspect.'):
            url = f'https://docs.python.org/3/library/inspect.html#{reference}'
            return f'[{link_text}]({url})'
        elif reference == 'frame-objects':
            url = 'https://docs.python.org/3/reference/datamodel.html#frame-objects'
            return f'[{link_text}]({url})'

        # Log unhandled reference-style links
        print(f"WARNING: Unhandled reference-style link: [{link_text}][{reference}]", file=sys.stderr)
        return match.group(0)

    # Match inline markdown links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace_inline_link, text)

    # Match reference-style markdown links: [text][reference]
    text = re.sub(r'\[([^\]]+)\]\[([^\]]+)\]', replace_reference_link, text)

    return text


def format_three_exclamation_notes(docstring: str) -> str:
    """
    Convert MkDocs admonitions into the alert syntax the docs theme renders.

    Receives a docstring that contains lines like:

        !!! warning "Deprecated"
        This method is now deprecated; use `model_copy` instead.

    And converts them to:

        > [!WARNING] Deprecated
        > This method is now deprecated; use `model_copy` instead.

    Two body layouts occur in the wild. Pydantic writes the body flush against
    the marker (as above); mkdocstrings indents it by four spaces, which is also
    how it carries fenced code blocks. Indented bodies are dedented rather than
    stripped, so their fences survive as real Markdown instead of collapsing
    into blockquote prose.

    Types whose body is normally a code block (``example``, ``quote``) become a
    bold lead-in instead of an alert, because a blockquote would swallow the
    fence. Unknown types keep their body and lose only the marker -- emitting
    ``!!! whatever`` verbatim just puts unrenderable syntax on the page.

    Also converts relative pydantic documentation links to absolute URLs.
    """
    # First convert any pydantic-relative links to absolute URLs
    docstring = convert_pydantic_links(docstring)

    lines = docstring.split("\n")
    result = []
    i = 0

    while i < len(lines):
        match = _ADMONITION_RE.match(lines[i])
        if not match:
            result.append(lines[i])
            i += 1
            continue

        kind = match.group("kind").lower()
        title = (match.group("title") or "").strip()
        if len(title) >= 2 and title[0] == '"' and title[-1] == '"':
            title = title[1:-1].strip()
        i += 1

        body, i = _collect_admonition_body(lines, i)

        if kind in _ADMONITION_LEADINS:
            lead = _ADMONITION_LEADINS[kind]
            result.append(f"**{lead} — {title}**" if title else f"**{lead}**")
            result.append("")
            result.extend(body)
        elif kind in _ADMONITION_ALERTS:
            heading, lead_in = _admonition_heading(kind, title)
            if lead_in:
                body = [lead_in, ""] + body if body else [lead_in]
            result.append(f"> [!{_ADMONITION_ALERTS[kind]}] {heading}".rstrip())
            result.extend(f"> {line}" if line.strip() else ">" for line in body)
        else:
            # Unknown admonition type: keep the content, drop the marker.
            if title:
                result.append(f"**{title}**")
                result.append("")
            result.extend(body)

        result.append("")

    return "\n".join(result)


def _collect_admonition_body(lines: list, i: int) -> tuple:
    """
    Return (body_lines, next_index) for the admonition starting at line ``i``.

    An indented block (mkdocstrings) is consumed whole and dedented by four, so
    blank lines and fenced code inside it are preserved. An unindented body
    (pydantic) runs to the first blank line, matching the original behaviour.
    """
    while i < len(lines) and not lines[i].strip():
        i += 1

    if i >= len(lines):
        return [], i

    if lines[i].startswith("    "):
        body, pending_blanks = [], []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                pending_blanks.append("")
                i += 1
            elif line.startswith("    "):
                body.extend(pending_blanks)
                pending_blanks = []
                body.append(line[4:])
                i += 1
            else:
                break
        return body, i

    body = []
    while i < len(lines) and lines[i].strip():
        body.append(lines[i].strip())
        i += 1
    return body, i


def _admonition_heading(kind: str, title: str) -> tuple:
    """
    Return (heading, lead_in) for an alert-style admonition.

    mkdocstrings packs the whole message into the title for ``deprecated``
    (e.g. ``"1.4.2 Use asdict instead."``), which is too long for a heading, so
    it moves into the body instead.
    """
    if kind == "deprecated":
        return "Deprecated", title
    if kind in ("version-added", "version-changed"):
        default = "Added in a later version" if kind == "version-added" else "Changed"
        return title or default, ""
    # Pydantic points at its own site for methods inherited from BaseModel.
    if title == "Usage Documentation":
        return "Usage Documentation (external docs for inherited method)", ""
    return title, ""


def test_convert_rst_roles():
    assert convert_rst_roles(":func:`tool` does a thing") == "`tool` does a thing"
    # A leading ~ means "display the last component only".
    assert convert_rst_roles(":class:`~pkg.mod.Name`") == "`Name`"
    assert convert_rst_roles(":class:`pkg.mod.Name`") == "`pkg.mod.Name`"
    # An explicit title wins over the target.
    assert convert_rst_roles(":meth:`the resolver <pkg.Thing.run>`") == "`the resolver`"
    assert convert_rst_roles(":mod:`pkg.testing` — helpers") == "`pkg.testing` — helpers"
    # Two roles on one line.
    assert convert_rst_roles(":func:`a` / :func:`b`") == "`a` / `b`"
    # Code fences are left alone.
    fenced = "```python\nx = ':class:`Foo`'\n```"
    assert convert_rst_roles(fenced) == fenced
    print("test_convert_rst_roles: ok")


def test_dedent_nested_sections():
    # A sync wrapper prepends its own summary to the wrapped __doc__, leaving
    # the borrowed body indented and its sections invisible to the parser.
    doc = (
        "Synchronous variant of run_agent.\n"
        "\n"
        "Run the agent and return the final text.\n"
        "\n"
        "    Call this from inside a task.\n"
        "\n"
        "    Args:\n"
        "        prompt: The user prompt.\n"
        "\n"
        "    Returns:\n"
        "        The final answer.\n"
    )
    fixed = dedent_nested_sections(doc)
    assert "\nArgs:\n" in fixed
    assert "\n    prompt: The user prompt.\n" in fixed
    assert "\nCall this from inside a task.\n" in fixed

    # A well-formed docstring is untouched.
    ok = "Do a thing.\n\nArgs:\n    x: A value.\n"
    assert dedent_nested_sections(ok) == ok

    # No sections at all: untouched.
    plain = "Just a sentence.\n\n    indented example\n"
    assert dedent_nested_sections(plain) == plain
    print("test_dedent_nested_sections: ok")


def test_format_three_exclamation_notes():
    # Pydantic style: body flush against the marker, ends at the blank line.
    out = format_three_exclamation_notes(
        '!!! warning "Deprecated"\nUse `model_copy` instead.\n\nOther text.'
    )
    assert "> [!WARNING] Deprecated" in out
    assert "> Use `model_copy` instead." in out
    assert "\nOther text." in out

    # A multi-word title survives (the old split(" ")[2] kept only "Deprecated").
    out = format_three_exclamation_notes('!!! warning "Deprecated since 1.0"\nGone soon.')
    assert "> [!WARNING] Deprecated since 1.0" in out

    # A bare marker keeps its body (the old code dropped the line entirely).
    out = format_three_exclamation_notes("!!! warning\nSomething to watch out for.")
    assert "> [!WARNING]" in out
    assert "> Something to watch out for." in out

    # mkdocstrings style: an indented code block must come out as real Markdown.
    out = format_three_exclamation_notes(
        '!!! example "`str` input"\n\n    ```python\n    f("b")\n    ```\n'
    )
    assert "**Example — `str` input**" in out
    assert "\n```python\n" in out
    assert '\nf("b")\n' in out
    assert "!!!" not in out

    # deprecated packs the message into the title; it belongs in the body.
    out = format_three_exclamation_notes('!!! deprecated "1.4.2 Use `asdict` instead."')
    assert "> [!WARNING] Deprecated" in out
    assert "> 1.4.2 Use `asdict` instead." in out

    out = format_three_exclamation_notes('!!! version-added "Added in `x` 0.3.0"')
    assert "> [!NOTE] Added in `x` 0.3.0" in out

    # Unknown types lose the marker but keep the content.
    out = format_three_exclamation_notes('!!! newthing "A Title"\n\n    body text\n')
    assert "!!!" not in out
    assert "**A Title**" in out
    assert "body text" in out
    print("test_format_three_exclamation_notes: ok")


def main():
    test_parse_params()
    test_parse_args()
    test_parse_google_sections()
    test_convert_rst_roles()
    test_dedent_nested_sections()
    test_format_three_exclamation_notes()


if __name__ == "__main__":
    main()
