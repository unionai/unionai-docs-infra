#!/usr/bin/env python3
"""Find candidate pages for a query by EXPANDED-TERM search, with context.

Replaces the title/slug-weighted scorer in draft_ideals.py, which was
systematically biased AGAINST canonical pages: a canonical page is titled with
the general concept ("Resources", section "Accelerators") while users search
the specific term ("gpu"), so it earned neither the title-match nor the
slug-match bonus and tied with incidental pages on raw frequency alone.

The pipeline instead is:
  1. an LLM expands the query into related vocabulary
     ("gpu" -> accelerator, tpu, nvidia, device, hardware, ...)
  2. this script greps the content for ALL of those terms
  3. it ranks by BREADTH -- how many distinct expansion terms a page uses --
     because a page that discusses gpu AND accelerator AND tpu AND device is
     about the topic, while one that says "gpu" 42 times and nothing else is
     usually a specific tutorial that merely runs on one
  4. it prints the matching lines in situ, so the LLM judges canonicality from
     the actual sentences rather than from a page's opening paragraph

Usage:
    find_candidates.py --content ../unionai-docs/content \
        --query gpu --terms gpu accelerator tpu nvidia "amd gpu" device hardware
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

URL_PREFIX = "/docs/v2/union/"
VARIANT = "union"
CATEGORY_MAP = {
    "user-guide": "guide", "api-reference": "reference", "tutorials": "tutorial",
    "deployment": "deployment", "oss-deployment": "deployment", "integrations": "guide",
}
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
FENCE_RE = re.compile(r"```.*?```", re.S)


def group_of(parts):
    cat = CATEGORY_MAP.get(parts[0] if parts else "", "other")
    return "other" if cat == "deployment" else cat


def url_for(rel_parts):
    parts = list(rel_parts)
    if parts[-1] in ("_index.md", "index.md"):
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".md")]
    return URL_PREFIX + ("/".join(parts) + "/" if parts else "")


def rank_key(p):
    """Combine the two signals; neither works alone.

    Breadth alone rescued generically-titled canonical pages ("Resources" for
    "gpu") but promoted pages that touch many adjacent concepts incidentally
    (a CI/CD guide beat the Secrets page because it mentions secret,
    credential, api key AND password). Title/heading match on the ORIGINAL
    query term is the canonicality signal; breadth is the about-it-vs-uses-it
    signal. Rank on both.
    """
    prime = 6 * p["prime_title"] + 3 * p["prime_heading"]
    return -(prime + p["breadth"] + min(p["total"], 40) / 20.0)


def scan(content_root, terms, primary=None):
    root = Path(content_root)
    pats = {t: re.compile(rf"(?<![\w-]){re.escape(t)}(?![\w-])", re.I) for t in terms}
    # the term the USER typed -- its presence in a title is the strongest
    # single indicator that a page is the canonical one for that query
    prime = primary or terms[0]
    prime_pat = re.compile(rf"(?<![\w-]){re.escape(prime)}s?(?![\w-])", re.I)
    out = []

    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        fm = FM_RE.match(text)
        title = ""
        if fm:
            if f"-{VARIANT}" in (fm.group(1)):
                # honour variant gating: a -union page can never be the answer
                if re.search(rf"variants:.*-{VARIANT}", fm.group(1)):
                    continue
            for line in fm.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.partition(":")[2].strip().strip("\"'")
            body = text[fm.end():]
        else:
            body = text

        body = FENCE_RE.sub(" ", body)
        headings = " ".join(re.findall(r"^#+\s+(.*?)\s*$", body, re.M))

        hits, total, where = {}, 0, set()
        for term, pat in pats.items():
            n = len(pat.findall(body))
            if n:
                hits[term] = n
                total += n
            if pat.search(title):
                where.add("title")
            if pat.search(headings):
                where.add("heading")
        if not hits:
            continue

        rel = list(path.relative_to(root).parts)
        # context lines: where the terms actually appear
        ctx = []
        for line in body.splitlines():
            s = line.strip()
            if len(s) < 25 or s.startswith(("|", ">", "#")):
                continue
            if sum(1 for p in pats.values() if p.search(s)) >= 1:
                ctx.append(re.sub(r"\s+", " ", s)[:150])
            if len(ctx) >= 3:
                break

        out.append({
            "prime_title": bool(prime_pat.search(title)),
            "prime_heading": bool(prime_pat.search(headings)),
            "url": url_for(rel), "title": title, "group": group_of(rel),
            "breadth": len(hits), "total": total, "where": where,
            "hits": hits, "ctx": ctx,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--terms", nargs="+", required=True)
    ap.add_argument("--per-group", type=int, default=3)
    args = ap.parse_args()

    pages = scan(args.content, args.terms, primary=args.query)
    pages.sort(key=rank_key)

    by_group = defaultdict(list)
    for p in pages:
        if len(by_group[p["group"]]) < args.per_group:
            by_group[p["group"]].append(p)

    print(f"QUERY {args.query!r}   expanded to: {', '.join(args.terms)}")
    for group in ("guide", "reference", "tutorial", "other"):
        if not by_group.get(group):
            continue
        print(f"\n  [{group}]")
        for p in by_group[group]:
            marks = "+".join(sorted(p["where"])) or "body-only"
            print(f"    - {p['url'].replace(URL_PREFIX, '')}")
            print(f"      title: {p['title']}   |  {p['breadth']} of "
                  f"{len(args.terms)} terms, {p['total']} hits, in {marks}")
            print(f"      terms: {dict(sorted(p['hits'].items(), key=lambda kv: -kv[1]))}")
            for c in p["ctx"][:2]:
                print(f"      > {c}")


if __name__ == "__main__":
    main()
