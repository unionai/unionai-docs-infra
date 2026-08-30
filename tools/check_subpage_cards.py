#!/usr/bin/env python3
"""Fail when a section page with children has no subpage cards.

WHY A CHECK RATHER THAN AUTOMATIC GENERATION (DOC-1509)

Cards are placed by an explicit `{{< subpage-cards >}}` marker, not injected,
because placement carries meaning: every section page that had cards put them
below its intro prose, and the generated API reference must have none at all
(its `## Directory` table already lists the same children).

The one thing implicit generation genuinely bought was that a new section page
could not ship with no navigation because its author did not know the
convention. This check buys that back without taking the placement decision away
from the author. Convention plus a gate -- the same shape as
check_deleted_pages.py and check_generated_content.py, both of which exist
because a convention on its own did not hold.

SCOPE

Authored content only. `content/api-reference/` is generated and deliberately
has no cards; running this over it would fail on 70 pages by design.

A page opts out with `subpage_cards: false` in its front matter. Use it for a
section whose landing page is a document in its own right rather than a
signpost -- and say why in a comment, because the next person will wonder.

Usage:
    check_subpage_cards.py [content-dir] [--exclude-dir PATH ...]
"""

import argparse
import re
import sys
from pathlib import Path

MARKER = re.compile(r"\{\{[<%]\s*subpage-cards\b")
LINK_CARD = re.compile(r"\{\{[<%]\s*link-card\b")
OPT_OUT = re.compile(r"^subpage_cards:\s*false\s*$", re.MULTILINE)

DEFAULT_EXCLUDES = ("api-reference", "__docs_builder__")


def frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[4:end] if end != -1 else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content", nargs="?", default="content", type=Path)
    ap.add_argument("--exclude-dir", action="append", default=[])
    ap.add_argument("--baseline", type=Path,
                    help="file of paths that predate this gate, one per line")
    args = ap.parse_args()

    if not args.content.is_dir():
        print(f"check-subpage-cards: no such directory: {args.content}", file=sys.stderr)
        return 2

    excludes = set(DEFAULT_EXCLUDES) | set(args.exclude_dir)

    baseline = set()
    if args.baseline and args.baseline.is_file():
        for line in args.baseline.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                baseline.add(line)

    missing, opted_out, legacy, grandfathered, ok = [], [], [], [], 0
    for index in sorted(args.content.rglob("_index.md")):
        rel = index.relative_to(args.content)
        if rel.parts and rel.parts[0] in excludes:
            continue
        d = index.parent
        has_child = any(p.is_dir() for p in d.iterdir()) or \
                    any(p.name != "_index.md" for p in d.glob("*.md"))
        if not has_child:
            continue

        text = index.read_text(encoding="utf-8")
        if OPT_OUT.search(frontmatter(text)):
            opted_out.append(str(rel))
        elif MARKER.search(text):
            ok += 1
        elif LINK_CARD.search(text):
            legacy.append(str(rel))
        elif str(rel) in baseline:
            grandfathered.append(str(rel))
        else:
            missing.append(str(rel))

    total = ok + len(opted_out) + len(legacy) + len(grandfathered) + len(missing)
    print(f"check-subpage-cards: {total} section page(s) with children "
          f"({ok} with cards, {len(opted_out)} opted out"
          + (f", {len(legacy)} still hand-written" if legacy else "")
          + (f", {len(grandfathered)} in the baseline" if grandfathered else "") + ")")

    if legacy:
        print("")
        print(f"NOTE: {len(legacy)} page(s) still use hand-written link-card blocks.")
        print("      Not a failure -- they render. Migrate to {{< subpage-cards >}}")
        print("      so a card and the page it points at cannot drift apart.")
        for p in legacy:
            print(f"  {p}")

    if not missing:
        print("check-subpage-cards: OK")
        return 0

    print("")
    print(f"FATAL: {len(missing)} section page(s) with children have no subpage cards.")
    print("       A reader landing there gets no way forward except the sidebar.")
    print("")
    print("       Add {{< subpage-cards >}} where the cards belong -- usually after")
    print("       the intro prose, not at the top. If this page genuinely should not")
    print("       have them, set `subpage_cards: false` in its front matter and say why.")
    print("")
    for p in missing:
        print(f"  {p}")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
