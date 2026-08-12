#!/usr/bin/env python3
"""Emit compact digests of candidate pages so a reader can judge canonicality.

The lexical scorer in draft_ideals.py ranks by title/slug/heading/frequency,
which cannot tell a page that DEFINES a topic from one that merely mentions it
a lot -- that is how "gpu" landed on a climate-modelling tutorial, and how a
152-152 tie silently picked the wrong page for "secret".

Judging canonicality needs the actual prose. Reading whole pages does not fit
in a review pass (45 queries x 4 groups x 3 candidates), so this emits a
digest per candidate: title, opening sentences, and section headings. That is
almost always enough to tell "this page is about X" from "this page uses X".

Usage:
    digest_candidates.py --queries queries.draft.json --content ../unionai-docs/content
    digest_candidates.py --queries queries.draft.json --content ... --only secret cache
    digest_candidates.py --queries queries.draft.json --content ... --ambiguous-only
"""

import argparse
import json
import re
from pathlib import Path

URL_PREFIX = "/docs/v2/union/"
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
FENCE_RE = re.compile(r"```.*?```", re.S)
SHORTCODE_RE = re.compile(r"\{\{[<%].*?[>%]\}\}", re.S)
OPENING_WORDS = 45
TIE_MARGIN = 15


def url_to_path(url, content_root):
    rel = url.replace(URL_PREFIX, "").strip("/")
    root = Path(content_root)
    for cand in (root / rel / "_index.md", root / f"{rel}.md"):
        if cand.is_file():
            return cand
    return None


def digest(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = FM_RE.match(text)
    title = ""
    if fm:
        for line in fm.group(1).splitlines():
            if line.startswith("title:"):
                title = line.partition(":")[2].strip().strip("\"'")
        body = text[fm.end():]
    else:
        body = text

    body = SHORTCODE_RE.sub(" ", FENCE_RE.sub(" ", body))
    headings = [h.strip() for h in re.findall(r"^##\s+(.*?)\s*$", body, re.M)]

    prose = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "|", "-", "*", "!")):
            continue
        prose.append(line)
        if len(" ".join(prose).split()) > OPENING_WORDS:
            break
    opening = " ".join(" ".join(prose).split()[:OPENING_WORDS])

    return title, opening, headings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--only", nargs="*", help="just these queries")
    ap.add_argument("--ambiguous-only", action="store_true",
                    help="only groups whose top two candidates are within TIE_MARGIN")
    ap.add_argument("--max-candidates", type=int, default=3)
    args = ap.parse_args()

    data = json.loads(Path(args.queries).read_text())
    if args.only:
        wanted = {q.lower() for q in args.only}
        data = [d for d in data if d["q"].lower() in wanted]

    for item in data:
        shown_any = False
        blocks = []
        for group, cands in item["candidates"].items():
            cands = cands[: args.max_candidates]
            if not cands:
                continue
            tie = len(cands) > 1 and (cands[0]["score"] - cands[1]["score"]) <= TIE_MARGIN
            if args.ambiguous_only and not tie:
                continue

            lines = [f"  [{group}]{'  << TIE' if tie else ''}"]
            for c in cands:
                path = url_to_path(c["url"], args.content)
                if not path:
                    lines.append(f"    - {c['url']}  (source not found)")
                    continue
                title, opening, headings = digest(path)
                short = c["url"].replace(URL_PREFIX, "")
                lines.append(f"    - {short}   [score {c['score']}]")
                lines.append(f"      title: {title}")
                lines.append(f"      opens: {opening}")
                if headings:
                    lines.append(f"      sections: {', '.join(headings[:8])}")
            blocks.append("\n".join(lines))
            shown_any = True

        if shown_any:
            print(f"\n{'=' * 78}\nQUERY: {item['q']!r}\n{'=' * 78}")
            print("\n".join(blocks))


if __name__ == "__main__":
    main()
