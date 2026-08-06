#!/usr/bin/env python3
"""Draft relevance labels for the search eval by grepping the CONTENT, not the index.

Deriving "ideal" results from the current index would bias the evaluation
toward the status quo -- the queries most worth fixing (a symbol that ranks a
tutorial above its own API page) would be labelled with the wrong answer and
the eval would score today's behaviour as perfect.

So candidates are scored against the markdown source: front-matter title,
filename slug, headings, then body frequency. Variant gating is honoured --
a page marked `-union` can never be the ideal for a variant:union query.

Output is a DRAFT for human review: every query carries its top candidates
with the evidence that produced them, plus a confidence flag.

Usage:
    draft_ideals.py --content ../unionai-docs/content --queries-from top.json \
                    --out queries.draft.json
"""

import argparse
import json
import re
from pathlib import Path

VARIANT = "union"
VERSION = "v2"
URL_PREFIX = f"/docs/{VERSION}/{VARIANT}"

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)
FENCE_RE = re.compile(r"```.*?```", re.S)


def parse_front_matter(text):
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def url_for(path, content_root):
    rel = path.relative_to(content_root)
    parts = list(rel.parts)
    if parts[-1] in ("_index.md", "index.md"):
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".md")]
    return f"{URL_PREFIX}/" + ("/".join(parts) + "/" if parts else "")


def load_pages(content_root):
    pages = []
    for path in sorted(Path(content_root).rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = parse_front_matter(text)

        variants = fm.get("variants", "")
        # `-union` gates the page out of the union variant entirely.
        if f"-{VARIANT}" in variants:
            continue

        body_nocode = FENCE_RE.sub(" ", body)
        headings = [h[1] for h in HEADING_RE.findall(body_nocode)]
        pages.append({
            "url": url_for(path, Path(content_root)),
            "title": fm.get("title", "").strip('"\''),
            "headings": headings,
            "body": body_nocode.lower(),
            "slug": path.stem.replace("-", " ").replace("_", " "),
        })
    return pages


def score(query, page):
    q = query.lower().strip()
    qwords = [w for w in re.split(r"[\s_]+", q) if w]
    title = page["title"].lower()
    slug = page["slug"].lower()
    why = []
    s = 0

    if title == q:
        s += 100; why.append("title==query")
    elif q in title:
        s += 55; why.append("title contains")
    elif all(w in title for w in qwords):
        s += 35; why.append("title has all words")

    if q.replace(" ", "") in slug.replace(" ", ""):
        s += 40; why.append("slug match")

    hits = sum(1 for h in page["headings"] if q in h.lower())
    if hits:
        s += min(hits, 3) * 15; why.append(f"{hits} heading(s)")

    freq = page["body"].count(q)
    if freq:
        s += min(freq, 12); why.append(f"body x{freq}")

    return s, why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True)
    ap.add_argument("--queries-from", required=True,
                    help="Algolia analytics top-searches JSON")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="additional curated queries")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", default="queries.draft.json")
    args = ap.parse_args()

    pages = load_pages(args.content)
    raw = json.load(open(args.queries_from)).get("searches") or []
    queries = [r["search"] for r in raw[: args.limit]] + list(args.extra)

    out = []
    for q in queries:
        scored = sorted(
            ((score(q, p), p) for p in pages),
            key=lambda t: -t[0][0],
        )[:4]
        scored = [(sc, why, p) for (sc, why), p in scored if sc > 0]
        if not scored:
            out.append({"q": q, "ideal": [], "confidence": "NONE",
                        "note": "no content match — likely a genuine gap"})
            continue

        top_score = scored[0][0]
        runner = scored[1][0] if len(scored) > 1 else 0
        # A clear winner is one that beats the runner-up decisively.
        confidence = ("high" if top_score >= 55 and top_score >= runner * 1.8
                      else "review")

        out.append({
            "q": q,
            "ideal": [scored[0][2]["url"]],
            "confidence": confidence,
            "candidates": [
                {"url": p["url"], "title": p["title"], "score": sc, "why": why}
                for sc, why, p in scored
            ],
        })

    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    hi = sum(1 for o in out if o["confidence"] == "high")
    rev = sum(1 for o in out if o["confidence"] == "review")
    none = sum(1 for o in out if o["confidence"] == "NONE")
    print(f"{len(pages)} union-visible pages scanned")
    print(f"{len(out)} queries -> {hi} high-confidence, {rev} need review, {none} no match")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
