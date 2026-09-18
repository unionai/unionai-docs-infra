#!/usr/bin/env python3
"""Fail on an <img> in the BUILT site whose src points at no file.

Why this exists (DOC-1515): every existing check reads a different artifact
than the browser does, so all of them can pass while a page ships a broken
image.

  check_images.sh          reads the SOURCE markdown against the filesystem,
                           and resolves relative paths from the markdown file's
                           own directory -- not from the page's URL.
  check_internal_links.py  reads content/, and skips images by design.
  check_generated_links.py reads the generated markdown twins in dist/, which
                           carry no <img> at all.
  check-asset-refs.sh      matches (css|js) only, is anchored at `="/`, and
                           runs under `|| true`.
  validate_urls.py         sees images, but treats any relative path as valid.

That gap was not theoretical. unionai/unionai-docs#1529 added an image to
content/api-reference/agent-plugins.md whose rendered src 404s on its own
preview, and ALL FIFTEEN of its checks passed.

WHAT MAKES IMAGES DIFFERENT FROM LINKS
--------------------------------------
A broken link 404s, and a 404 is loud. A broken image can be silent twice over:

  1. The browser resolves the src against the PAGE's URL, not the source file's
     directory. Hugo's render hooks do that rebasing, so a src can be correct in
     the markdown and wrong in the HTML. Only the built tree shows the truth.
  2. A wrong path often lands on something that is not a 404. The defect this
     check was written for produced /docs/v2/_static/..., which 302s to an HTML
     page. Status-code checking would call that fine; the reader sees a broken
     image icon.

So this resolves the way a browser does, and asserts the target is a real FILE
-- not a directory, and not an HTML page.

Usage:
    check_rendered_images.py [--dist dist] [--exclude FILE] [--quiet]
"""

import argparse
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import unquote

# src on an <img>. Attribute order varies, so match the tag then the attribute.
IMG_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)

# Never resolvable against the built tree, and never our bug.
EXTERNAL = ("http://", "https://", "//", "data:", "mailto:", "#")

# A target that exists but is one of these is not an image. Pointing an <img>
# at a page is the failure mode that does not 404, so name it separately.
NOT_AN_IMAGE = (".html", ".htm")


def page_url_dir(html: Path, dist: Path) -> str:
    """The directory a browser resolves a relative src against.

    For pretty URLs (`about/index.html`) the browser's base is `/about/`, so
    relative srcs resolve against the file's own directory. For an ugly URL
    (`about.html`) the base is the parent. Both reduce to "the directory the
    HTML file sits in", because index.html IS in the directory it serves.
    """
    return "/" + str(html.parent.relative_to(dist)).replace("\\", "/").strip("/")


def resolve(src: str, html: Path, dist: Path) -> Path | None:
    """Filesystem path a browser would fetch, or None if it escapes the site.

    The `..` segments are walked by hand rather than handed to
    `posixpath.normpath`, which CLAMPS a leading `..` at the root of an absolute
    path: it turns `/a/../../../b` into `/b` instead of reporting the escape. A
    clamped path can name a file that exists, so the escape would not merely be
    mislabelled -- it would PASS. Servers clamp the same way, but a src that
    needs clamping to resolve is a defect either way, and the point of this tool
    is to say so.
    """
    src = unquote(src.split("?", 1)[0].split("#", 1)[0])
    base = page_url_dir(html, dist) + "/"
    absolute = src if src.startswith("/") else posixpath.join(base, src)

    parts: list[str] = []
    for seg in absolute.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return dist.joinpath(*parts) if parts else dist


def classify(target: Path | None) -> str:
    if target is None:
        return "escapes"
    if target.is_dir():
        return "directory"
    if not target.is_file():
        return "missing"
    if target.suffix.lower() in NOT_AN_IMAGE:
        return "html"
    return "ok"


REASON = {
    "escapes": "resolves outside the site root",
    "directory": "resolves to a directory",
    "missing": "no such file in the build",
    "html": "resolves to an HTML page, not an image",
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", default="dist", type=Path,
                    help="site root -- the directory an absolute src is "
                         "resolved against (default: dist)")
    ap.add_argument("--exclude", type=Path,
                    help="file of dist-relative page paths to ignore, one per "
                         "line, # for comments")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    dist = args.dist
    if not dist.is_dir():
        print(f"check-rendered-images: no such directory: {dist}", file=sys.stderr)
        print("Run `make dist` first; this checks the BUILT tree, not content/.",
              file=sys.stderr)
        return 2

    excluded = set()
    if args.exclude and args.exclude.is_file():
        for line in args.exclude.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                excluded.add(line)

    broken, total, external, skipped = [], 0, 0, 0

    for html in sorted(dist.rglob("*.html")):
        rel = str(html.relative_to(dist))
        try:
            text = html.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for src in IMG_RE.findall(text):
            if not src.strip():
                continue
            total += 1
            if src.startswith(EXTERNAL):
                external += 1
                continue
            if rel in excluded:
                skipped += 1
                continue
            verdict = classify(resolve(src, html, dist))
            if verdict != "ok":
                broken.append((rel, src, verdict))

    if not args.quiet or broken:
        print(f"check-rendered-images: {total} <img> in the built site")
        print(f"  {external} external / data URIs -- not checked")
        if skipped:
            print(f"  {skipped} excluded by {args.exclude}")

    if not broken:
        print("check-rendered-images: OK")
        return 0

    print("")
    print(f"FATAL: {len(broken)} rendered image(s) point at no file.")
    print("       Resolved the way a browser does -- against the PAGE's URL, not")
    print("       the source file's directory. check_images.sh cannot see this.")
    print("")
    for rel, src, verdict in broken:
        print(f"  {rel}")
        print(f"      src={src}")
        print(f"      -> {REASON[verdict]}")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
