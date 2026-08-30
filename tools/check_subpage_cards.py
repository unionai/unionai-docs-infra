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

# A hand-authored card plus the body line under it.
CARD_WITH_BODY = re.compile(
    r"\{\{[<%]\s*link-card[^}]*target=\"([^\"]+)\"[^}]*[>%]\}\}\n([^\n]*)")

# The child's own one-line description, which the card body should equal.
DESCRIPTION = re.compile(r"^description:\s*(.*)$", re.MULTILINE)
ICON = re.compile(r"^icon:\s*(.*)$", re.MULTILINE)

# The whole shortcode tag, for reading its attributes.
CARD_TAG = re.compile(r"\{\{[<%]\s*link-card([^}]*)[>%]\}\}")

DEFAULT_EXCLUDES = ("api-reference", "__docs_builder__")


def description_of(page_dir: Path, name: str) -> str:
    """The `description` of the child served at `name`, or "" if it has none."""
    for cand in (page_dir / name / "_index.md", page_dir / f"{name}.md"):
        if cand.is_file():
            m = DESCRIPTION.search(frontmatter(cand.read_text(encoding="utf-8")))
            return m.group(1).strip().strip("'\"") if m else ""
    return ""


def _attr(blob: str, key: str) -> str:
    m = re.search(rf'{key}="([^"]*)"', blob)
    return m.group(1) if m else ""


def icon_of(page_dir: Path, name: str) -> str:
    for cand in (page_dir / name / "_index.md", page_dir / f"{name}.md"):
        if cand.is_file():
            m = ICON.search(frontmatter(cand.read_text(encoding="utf-8")))
            return m.group(1).strip().strip("'\"") if m else ""
    return ""


def children_of(page_dir: Path) -> set:
    """Immediate children, by the name each is served under."""
    return ({p.name for p in page_dir.iterdir() if p.is_dir()} |
            {p.stem for p in page_dir.glob("*.md") if p.name != "_index.md"})


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

    # Two entry shapes share one file: a bare path exempts a whole page from
    # needing cards; `page -> child` exempts one child from needing a card on
    # that page. Both shrink; neither should ever be added to silence a new
    # finding.
    baseline = set()
    if args.baseline and args.baseline.is_file():
        for line in args.baseline.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                baseline.add(line)

    missing, opted_out, legacy, grandfathered, ok = [], [], [], [], 0
    uncovered, out_of_sync, iconless, twins = [], [], [], []
    for index in sorted(args.content.rglob("_index.md")):
        rel = index.relative_to(args.content)
        if rel.parts and rel.parts[0] in excludes:
            continue
        d = index.parent
        has_child = any(p.is_dir() for p in d.iterdir()) or \
                    any(p.name != "_index.md" for p in d.glob("*.md"))
        if not has_child:
            continue

        # Two children sharing an icon sit side by side in one grid looking
        # identical, so the icon distinguishes nothing and is pure noise. This
        # is the only icon problem a reader can see, and check_icon_names.py
        # cannot see it: every name involved is perfectly valid.
        seen = {}
        for name in sorted(children_of(d)):
            i = icon_of(d, name)
            if i:
                seen.setdefault(i, []).append(name)
        for i, names in sorted(seen.items()):
            if len(names) > 1:
                twins.append((str(rel), i, names))

        text = index.read_text(encoding="utf-8")
        if OPT_OUT.search(frontmatter(text)):
            opted_out.append(str(rel))
        elif MARKER.search(text):
            ok += 1
        elif LINK_CARD.search(text):
            legacy.append(str(rel))
            # A hand-authored card set is a permanent, first-class choice, not a
            # migration backlog. But it opts out of the two things generation
            # would have done for free, so check them here instead: that the
            # cards still cover every child, and that each still says what the
            # child says about itself.
            kids = children_of(d)
            carded = set()
            for target, body in CARD_WITH_BODY.findall(text):
                name = target.strip("./").split("/")[0]
                carded.add(name)
                want = description_of(d, name)
                if want and body.strip() and body.strip() != want:
                    out_of_sync.append((str(rel), name, body.strip(), want))
            for name in sorted(kids - carded):
                if f"{rel} -> {name}" not in baseline:
                    uncovered.append((str(rel), name))
            for card in CARD_TAG.findall(text):
                name = _attr(card, "target").strip("./").split("/")[0]
                have, want = _attr(card, "icon"), icon_of(d, name)
                if have and want and have != want:
                    out_of_sync.append((str(rel), name, f"icon={have}", f"icon={want}"))
                elif not have and want:
                    iconless.append((str(rel), name, want))
        elif str(rel) in baseline:
            grandfathered.append(str(rel))
        else:
            missing.append(str(rel))

    total = ok + len(opted_out) + len(legacy) + len(grandfathered) + len(missing)
    print(f"check-subpage-cards: {total} section page(s) with children "
          f"({ok} with cards, {len(opted_out)} opted out"
          + (f", {len(legacy)} hand-authored" if legacy else "")
          + (f", {len(grandfathered)} in the baseline" if grandfathered else "") + ")")

    if iconless:
        print("")
        print(f"NOTE: {len(iconless)} hand-authored card(s) have no icon, but the page")
        print("      they point at has one in its front matter. The card renders")
        print("      without it. Adding icon=\"...\" to the card would show it.")
        for rel, name, want in iconless[:12]:
            print(f"  {rel} -> {name}  (page has icon: {want})")
        if len(iconless) > 12:
            print(f"  ... and {len(iconless) - 12} more")

    if not missing and not uncovered and not out_of_sync and not twins:
        print("check-subpage-cards: OK")
        return 0

    if twins:
        print("")
        print(f"FATAL: {len(twins)} set(s) of sibling pages share an icon.")
        print("       Their cards sit side by side in one grid looking identical, so")
        print("       the icon distinguishes nothing. Every name is valid, which is")
        print("       why check-icon-names cannot see this.")
        print("")
        for rel, icon, names in twins:
            print(f"  {rel}")
            print(f"      {icon}: {', '.join(names)}")
        print("")

    if out_of_sync:
        print("")
        print(f"FATAL: {len(out_of_sync)} hand-authored card(s) disagree with the page they")
        print("       point at. A reader is told one thing on the landing page and")
        print("       another on the page itself, and nothing else would catch it.")
        print("")
        for rel, name, have, want in out_of_sync:
            print(f"  {rel} -> {name}")
            print(f"      card: {have}")
            print(f"      page: {want}")
        print("")

    if uncovered:
        print("")
        print(f"FATAL: {len(uncovered)} child page(s) have no card on their parent.")
        print("       A hand-authored card set does not grow by itself, so a page added")
        print("       later is reachable only from the sidebar.")
        print("")
        for rel, name in uncovered:
            print(f"  {rel} -> {name}")
        print("")

    if not missing:
        return 1

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
