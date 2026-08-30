#!/usr/bin/env python3
"""Fail on a link in the GENERATED markdown that points at nothing.

Why this exists (DOC-1499, DOC-1511): `check_internal_links.py` scans `content/`
-- the Hugo SOURCE. Nothing checked the links the llms_generator WRITES into
`dist/`, and those are different links: the generator rewrites every relative
link to an absolute URL, inlines pages into bundles, and copies child headings
into a parent's `## Subpages` listing.

That gap was not theoretical. A heading copied into a parent kept a URL written
against the child, so it resolved one level too high. **202 links in the shipped
twins pointed at nothing** while `make check-links` printed "All internal links
are valid", because it was reading a different set of files. One of them turned a
404 into a confident 200 on the wrong page.

The markdown twins are the surface AI agents read. A dead link there is followed
by a machine that cannot tell it went somewhere wrong.

SCOPE
-----
A build holds only the version/variant trees it built (usually one version, two
variants). A link into a tree that is not in this build cannot be checked here
and is NOT a failure -- it is reported separately so the number stays visible.

Usage:
    check_generated_links.py [--dist dist/docs] [--exclude FILE] [--quiet]
"""

import argparse
import re
import sys
from pathlib import Path

# A markdown link whose target is an absolute docs URL. The generator makes every
# internal link absolute, so a relative one here means a pass was skipped.
LINK_RE = re.compile(r'\[([^\]]*)\]\((https://www\.union\.ai/docs/[^)\s]+)\)')

DOCS_PREFIX = "https://www.union.ai/docs/"

# Markdown link syntax is greedy and code samples contain parentheses, so a
# fenced snippet can be captured as a link. These never occur in a real URL.
NOT_A_URL = ("`", "{{", "**", "=", ",", "<", ">", '"', "'")


def looks_like_code(url: str) -> bool:
    return any(t in url for t in NOT_A_URL)


def build_scope(dist: Path) -> set:
    """The (version, variant) trees this build actually contains."""
    scope = set()
    for version in dist.iterdir():
        if not version.is_dir():
            continue
        for variant in version.iterdir():
            if variant.is_dir():
                scope.add(f"{version.name}/{variant.name}")
    return scope


def in_scope(path: str, scope: set) -> bool:
    parts = path.split("/")
    return len(parts) >= 2 and f"{parts[0]}/{parts[1]}" in scope


def resolves(dist: Path, path: str) -> bool:
    """A URL path resolves if the build holds a page, a directory, or a twin."""
    target = dist / path
    return target.is_file() or target.is_dir() or (dist / (path + ".md")).is_file()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", default="dist/docs", type=Path)
    ap.add_argument("--exclude", type=Path,
                    help="file of URL paths to ignore, one per line, # for comments")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.dist.is_dir():
        print(f"check-generated-links: no such directory: {args.dist}", file=sys.stderr)
        print("Run `make dist` first; this checks the BUILT tree, not content/.", file=sys.stderr)
        return 2

    excluded = set()
    if args.exclude and args.exclude.is_file():
        for line in args.exclude.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                excluded.add(line)

    scope = build_scope(args.dist)
    broken, out_of_scope, code_like, skipped = [], 0, 0, 0
    total = 0

    for md in sorted(args.dist.rglob("*.md")):
        rel = str(md.relative_to(args.dist))
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        for _, url in LINK_RE.findall(text):
            total += 1
            path = url.split("#")[0][len(DOCS_PREFIX):]
            if looks_like_code(url):
                code_like += 1
                continue
            if not in_scope(path, scope):
                out_of_scope += 1
                continue
            if path in excluded:
                skipped += 1
                continue
            if not resolves(args.dist, path):
                broken.append((rel, url))

    if not args.quiet or broken:
        print(f"check-generated-links: {total} links in the built markdown "
              f"({len(scope)} tree(s): {', '.join(sorted(scope))})")
        print(f"  {out_of_scope} outside this build (other version/variant) -- not checked")
        print(f"  {code_like} captured from code samples, not URLs -- not checked")
        if skipped:
            print(f"  {skipped} excluded by {args.exclude}")

    if not broken:
        print("check-generated-links: OK")
        return 0

    print("")
    print(f"FATAL: {len(broken)} generated link(s) point at nothing.")
    print("       These are in the markdown AI agents read, and nothing else checks them:")
    print("       check_internal_links.py reads content/, not the built tree.")
    print("")
    for rel, url in broken:
        print(f"  {rel}")
        print(f"      -> {url}")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
