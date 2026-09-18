"""Bootstrap Icons names for the pages the API generator emits.

Why this file validates instead of trusting the caller: our shortcodes and
frontmatter feed `<sl-icon name="...">`, which Shoelace resolves against
Bootstrap Icons on the CDN. An unknown name fetches 404 and renders an EMPTY
SLOT -- no build error, no broken link, nothing the link checker can see. Four
dead names (written against Lucide's vocabulary) accumulated across nine call
sites on two version lines that way, one of them on the page that teaches the
shortcode (DOC-1444).

A generated page is the worse version of that problem, because one wrong name
here is not one blank slot, it is a blank slot on every page of a kind. So the
name is checked against the vendored set as it is emitted, and a miss raises
rather than warns: a generator that fails is fixable, a generator that ships
2xx blank icons is not visible until a reader mentions it.

The per-kind map is deliberately coarse. Guessing an icon per symbol means 300
guesses nobody validates; five kinds mean five decisions a reviewer can hold in
their head, and the kinds are exactly the distinctions the reader is making when
scanning a list of subpages.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional
import re

# .../tools/api_generator/lib/generate/icons.py
_TOOLS = Path(__file__).resolve().parents[3]
_INFRA_ROOT = _TOOLS.parent
ICON_SETS = _TOOLS / "icon_sets"
BASEOF = _INFRA_ROOT / "layouts" / "_default" / "baseof.html"

AUTOLOADER = re.compile(r"@shoelace-style/shoelace@(\d+\.\d+\.\d+)/cdn/shoelace-autoloader\.js")

# One icon per page KIND, not per symbol.
#
#   section    the API reference for one distribution -- a reference volume
#   module     Bootstrap's package glyph; a module is a package of symbols
#   class      `{}`, the glyph for a code object you can instantiate
#   protocol   a contract several types implement -- a hierarchy, not an object
#   exception  a failure
#
# Every value is checked against the vendored Bootstrap set on use; see
# tests/test_api_generator_icons.py, which asserts the whole map resolves.
PAGE_ICONS = {
    "section": "book",
    "module": "box-seam",
    "class": "braces",
    "protocol": "diagram-3",
    "exception": "exclamation-triangle",
}


def pinned_version() -> Optional[str]:
    """The Shoelace version baseof.html loads the autoloader from."""
    if not BASEOF.is_file():
        return None
    m = AUTOLOADER.search(BASEOF.read_text(encoding="utf-8"))
    return m.group(1) if m else None


@lru_cache(maxsize=1)
def icon_set() -> frozenset:
    """The vendored Bootstrap Icons names for the pinned Shoelace version.

    Vendored rather than fetched, so the check is deterministic and works
    offline -- a network hiccup can never turn it green.
    """
    version = pinned_version()
    candidates = []
    if version:
        candidates.append(ICON_SETS / f"shoelace-{version}.txt")
    candidates.extend(sorted(ICON_SETS.glob("shoelace-*.txt")))
    for path in candidates:
        if path.is_file():
            names = {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
            if names:
                return frozenset(names)
    raise FileNotFoundError(
        f"no vendored Bootstrap Icons list under {ICON_SETS}. "
        "Refresh it with `make update-icon-names`."
    )


def validate_icon(name: str) -> str:
    """Return `name`, or raise if Shoelace cannot resolve it."""
    if name not in icon_set():
        raise ValueError(
            f"icon {name!r} is not a Bootstrap Icons name "
            f"(Shoelace {pinned_version() or '?'}). It would render an empty slot "
            "with no error. See https://icons.getbootstrap.com/"
        )
    return name


def icon_for(kind: str) -> str:
    """The icon for a page kind, validated."""
    try:
        name = PAGE_ICONS[kind]
    except KeyError:
        raise KeyError(f"no icon defined for page kind {kind!r}; known kinds: "
                       f"{', '.join(sorted(PAGE_ICONS))}") from None
    return validate_icon(name)


def class_icon_kind(info) -> str:
    """Which kind a class page is: an exception, a protocol, or a plain class."""
    if info.get("is_exception"):
        return "exception"
    if info.get("parent") == "Protocol":
        return "protocol"
    return "class"
