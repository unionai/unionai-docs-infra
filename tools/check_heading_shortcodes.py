#!/usr/bin/env python3
"""Fail on a heading that calls a shortcode with the `{{< ... >}}` delimiters.

Why (DOC-1357): Hugo swaps every `{{< shortcode >}}` for an internal placeholder
(HAHAHUGOSHORTCODE<n>s<m>HBHB) before goldmark parses the page, and the <n>
counter is assigned per build. A heading containing one therefore gets an
auto-generated id built out of that placeholder, so the anchor reads as garbage
and, worse, changes on every deploy. Deep links into it die.

Hugo resolves the `{{% ... %}}` form BEFORE goldmark instead, inlining the
result into the markdown, so the id derives from the real heading text and stays
put. An explicit `{#my-id}` on the heading works too.

The render-heading hook keeps the id stable either way, but the anchor it
salvages is missing the shortcode's words. This check is what keeps the good
anchors good.

Usage: check_heading_shortcodes.py [content-dir ...]   (default: content)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r"^#{1,6}\s")
FENCE = re.compile(r"^\s*(```+|~~~+)")
# A real shortcode call, not the `{{</* ... */>}}` form used to DISPLAY one.
SHORTCODE = re.compile(r"\{\{<(?!/\*)")


def offenders(root: Path) -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.md")):
        in_fence = False
        fence_char = ""
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            fence = FENCE.match(line)
            if fence:
                char = fence.group(1)[0]
                if not in_fence:
                    in_fence, fence_char = True, char
                elif char == fence_char:
                    in_fence, fence_char = False, ""
                continue
            if in_fence or not HEADING.match(line):
                continue
            if SHORTCODE.search(line):
                found.append((path, lineno, line.strip()))
    return found


def main() -> int:
    roots = [Path(a) for a in sys.argv[1:]] or [Path("content")]
    found: list[tuple[Path, int, str]] = []
    for root in roots:
        if not root.is_dir():
            print(f"check-heading-shortcodes: no such directory: {root}", file=sys.stderr)
            return 2
        found += offenders(root)

    if not found:
        print("check-heading-shortcodes: OK (no heading uses the {{< ... >}} form)")
        return 0

    print("FATAL: these headings call a shortcode with `{{< ... >}}`, which gives them")
    print("       a build-varying anchor id (DOC-1357). Switch the delimiters to")
    print("       `{{% ... %}}`, or give the heading an explicit `{#my-id}`.")
    print("")
    for path, lineno, line in found:
        print(f"  {path}:{lineno}: {line}")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
