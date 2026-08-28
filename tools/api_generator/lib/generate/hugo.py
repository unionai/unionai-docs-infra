import io
from typing import List, TypedDict, Optional
from sys import stderr

from lib.generate.icons import validate_icon


version = "0.0.0"
variants = ""


def normalize_variants(v) -> str:
    """Render a variants spec as Hugo frontmatter, with an explicit +/- per name.

    The `variants:` line gates page visibility, and the gate reads a sign on
    every name: `+flyte +union` includes both, `-flyte +union` is union-only.
    A bare name carries no sign, so the page matches no variant and silently
    disappears from every build -- which also breaks any link pointing at it.

    Callers pass either a pre-signed string ("+flyte +union") or a bare list
    (["flyte", "union"]). Signing here, at the single point where the line is
    written, means a caller cannot reintroduce the unsigned form.
    """
    tokens = v if isinstance(v, list) else (v or "").split()
    return " ".join(t if t[0] in "+-" else f"+{t}" for t in (t.strip() for t in tokens) if t)


def set_variants(v):
    global variants
    variants = normalize_variants(v)


def set_version(v: str):
    global version
    version = v

class FrontMatterExtra(TypedDict):
    weight: Optional[int]
    expand_sidebar: Optional[bool]


def yaml_quote(value: str) -> str:
    """Render a string as a YAML double-quoted scalar.

    A description is prose we did not write: it can open with `#`, carry a
    colon, a quote or a backslash, and any of those turns an unquoted
    frontmatter value into a parse error or a silently different string.
    Quoting every one of them costs nothing and removes the whole class.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_front_matter(title: str, output: io.TextIOWrapper, extra: Optional[FrontMatterExtra] = None,
                       description: Optional[str] = None, icon: Optional[str] = None):
    """Write a generated page's frontmatter.

    `description` and `icon` are OMITTED when absent rather than written empty:
    a symbol with no docstring has no description, and the consuming card must
    handle that. An empty string would be a lie the card cannot tell from a
    real one.
    """
    output.write("---\n")
    output.write(f"title: {title}\n")
    if description:
        output.write(f"description: {yaml_quote(description)}\n")
    if icon:
        output.write(f"icon: {validate_icon(icon)}\n")
    output.write(f"version: {version}\n")
    output.write(f"variants: {variants}\n")
    output.write("layout: py_api\n")
    if extra:
        if extra['weight']:
            output.write(f"weight: {extra['weight']}\n")
        if extra['expand_sidebar']:
            output.write("sidebar_expanded: true\n")
    output.write("---\n\n")


def test_normalize_variants():
    # Bare names get signed. This is the DOC-1394 case: an unsigned line gates
    # the page out of every variant, so it vanishes and inbound links break.
    assert normalize_variants(["flyte", "union"]) == "+flyte +union"
    assert normalize_variants("flyte union") == "+flyte +union"
    # Already-signed input is left exactly as it is, including exclusions.
    assert normalize_variants("+flyte +union") == "+flyte +union"
    assert normalize_variants("-flyte +union") == "-flyte +union"
    assert normalize_variants(["-flyte", "+union"]) == "-flyte +union"
    # Mixed, ragged whitespace, and empties.
    assert normalize_variants("flyte  +union") == "+flyte +union"
    assert normalize_variants("") == ""
    assert normalize_variants(None) == ""
    assert normalize_variants([]) == ""
    print("test_normalize_variants: ok")


if __name__ == "__main__":
    test_normalize_variants()
