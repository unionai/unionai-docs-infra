#!/usr/bin/env python3
"""Generate Algolia search records from the built docs.

Reads the SERVED markdown artifact -- dist/docs/<version>/<variant>/**/page.md --
rather than crawling the site or reading the pre-shortcode tmp-md tree:

  * page.md is post-shortcode-processing, so records hold resolved prose
    rather than raw shortcode markup.
  * The dist path carries both facets, so `variant` and `version` never have
    to be re-derived from a URL by regex.
  * Index membership therefore follows what the build actually produced --
    not what versions.toml declares, and not what the CDN happens to still
    be serving from cache.

Records use the DocSearch schema (hierarchy.lvl0-6, content, anchor, url,
weight) so the existing frontend and index settings keep working unchanged.

Usage:
    build_records.py --dist dist --out records.json
    build_records.py --dist dist --out records.json --max-level 3
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# Aggregate files restate page content; indexing them duplicates every page's
# prose under the wrong URL.
SKIP_NAMES = {"section.md", "llms.txt", "llms-full.txt", "index.md"}

DOCS_BASE = "https://www.union.ai/docs"

# Category is derived from the first path segment, with an explicit catch-all so
# the taxonomy is EXHAUSTIVE: every record lands in exactly one bucket and no
# content can become unreachable when results are grouped or filtered by it.
CATEGORY_MAP = {
    "user-guide": ("guide", "User guide"),
    "api-reference": ("reference", "API reference"),
    "tutorials": ("tutorial", "Tutorials"),
    "deployment": ("deployment", "Deployment"),
    "oss-deployment": ("deployment", "Deployment"),
    # Top-level /integrations/ is user-facing integration documentation, distinct
    # from api-reference/integrations/* (which maps to `reference` via its first
    # segment). Grouping it with `guide` keeps "spark"/"snowflake" out of the
    # leftovers bucket, while its own lvl0 label still gives it a display heading.
    "integrations": ("guide", "Integrations"),
}
CATEGORY_FALLBACK = ("other", "Other")


def categorise(url_path):
    """(category, section_label) for a docs path -- total by construction."""
    parts = url_path.split("/")
    segment = parts[2] if len(parts) > 2 else ""
    return CATEGORY_MAP.get(segment, CATEGORY_FALLBACK), segment

FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# Algolia rejects any record over 10 KB. A long section (a big API table, a
# reference page with one huge body) blows that limit, so content is CHUNKED
# rather than truncated -- truncating would silently make the tail of a long
# page unsearchable, which is worse than carrying an extra record.
MAX_CONTENT_BYTES = 7000


def chunk_content(text, limit=MAX_CONTENT_BYTES):
    """Split prose into <=limit-byte pieces on sentence/word boundaries."""
    if not text:
        return []
    if len(text.encode("utf-8")) <= limit:
        return [text]

    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate.encode("utf-8")) > limit:
            if current:
                chunks.append(current)
                current = sentence
            else:
                # A single sentence over the limit: fall back to a hard split.
                raw = sentence.encode("utf-8")
                while len(raw) > limit:
                    cut = raw[:limit].rsplit(b" ", 1)[0] or raw[:limit]
                    chunks.append(cut.decode("utf-8", "ignore"))
                    raw = raw[len(cut):].lstrip()
                current = raw.decode("utf-8", "ignore")
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def slugify(text):
    """Reproduce Hugo's default (github-style) heading anchors.

    "Type hints are required" -> "type-hints-are-required"
    Anchors must match the ids Hugo emits or every deep link lands at the
    top of the page instead of the section.
    """
    text = text.strip().lower()
    # Drop inline markdown emphasis/code/link syntax before slugifying.
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}", "", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def strip_markdown(text):
    """Flatten markdown to plain prose for the `content` attribute."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"[*_]{1,3}", "", text)
    text = re.sub(r"^\s*\|.*$", " ", text, flags=re.M)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_sections(md):
    """Split markdown into (level, title, anchor, body) sections.

    Headings inside fenced code blocks are NOT headings -- a python comment
    like `# Direct call - runs locally` would otherwise become a phantom
    record with an anchor that does not exist in the rendered page.
    """
    sections = []
    current = None
    in_fence = False
    fence_marker = None
    seen_anchors = {}

    for line in md.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, None
            if current is not None:
                current["body"].append(line)
            continue

        if in_fence:
            if current is not None:
                current["body"].append(line)
            continue

        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if not title:
                continue
            anchor = slugify(title)
            # Hugo disambiguates repeated headings with -1, -2, ...
            if anchor in seen_anchors:
                seen_anchors[anchor] += 1
                anchor = f"{anchor}-{seen_anchors[anchor]}"
            else:
                seen_anchors[anchor] = 0
            current = {"level": level, "title": title, "anchor": anchor, "body": []}
            sections.append(current)
        elif current is not None:
            current["body"].append(line)

    return sections


def iter_pages(dist):
    """Yield (page_md_path, version, variant, url_path) for every built page."""
    docs_root = Path(dist) / "docs"
    if not docs_root.is_dir():
        raise SystemExit(f"no docs tree under {docs_root}")

    for path in sorted(docs_root.rglob("page.md")):
        if path.name in SKIP_NAMES:
            continue
        rel = path.relative_to(docs_root)
        parts = rel.parts
        # <version>/<variant>/<...>/page.md
        if len(parts) < 3:
            continue
        version, variant = parts[0], parts[1]
        if variant not in ("union", "flyte"):
            continue
        url_path = "/".join(parts[:-1])
        yield path, version, variant, url_path


def records_for_page(md, version, variant, url_path, max_level):
    (category, section_label), _seg = categorise(url_path)
    sections = parse_sections(md)
    if not sections:
        return []

    base_url = f"{DOCS_BASE}/{url_path}/"
    page_title = sections[0]["title"] if sections[0]["level"] == 1 else url_path.split("/")[-1]

    records = []
    stack = {}
    for position, sec in enumerate(sections):
        level = sec["level"]
        if level > max_level:
            continue

        stack[level] = sec["title"]
        for deeper in [k for k in stack if k > level]:
            del stack[deeper]

        hierarchy = {f"lvl{i}": None for i in range(7)}
        # lvl0 is the section, not a constant: DocSearch groups results under
        # lvl0 headings, so a constant throws that grouping away for free.
        hierarchy["lvl0"] = section_label
        hierarchy["lvl1"] = page_title
        for lvl, title in stack.items():
            if lvl > 1:
                hierarchy[f"lvl{lvl}"] = title

        is_page_root = position == 0 and level == 1
        url = base_url if is_page_root else f"{base_url}#{sec['anchor']}"
        pieces = chunk_content(strip_markdown("\n".join(sec["body"]))) or [None]

        for n, piece in enumerate(pieces):
            # Chunks of one section share its anchor and hierarchy; only the
            # objectID differs, so deep links stay correct.
            oid = url if n == 0 else f"{url}::{n}"
            records.append({
                "objectID": hashlib.sha1(oid.encode()).hexdigest(),
                "url": url,
                "url_without_anchor": base_url,
                "anchor": None if is_page_root else sec["anchor"],
                "content": piece,
                "hierarchy": hierarchy,
                "type": f"lvl{level}" if n == 0 else "content",
                "variant": variant,
                "version": version,
                "category": category,
                "weight": {
                    "pageRank": 0,
                    "level": 100 - (level * 10),
                    "position": position,
                },
            })

    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist", help="path to the built dist/ tree")
    ap.add_argument("--out", default="records.json")
    ap.add_argument("--max-level", type=int, default=6,
                    help="deepest heading level to index (lower = fewer records)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    all_records = []
    pages = 0
    per_slice = {}
    cat_stats = {}
    other_segments = {}

    for path, version, variant, url_path in iter_pages(args.dist):
        md = path.read_text(encoding="utf-8", errors="replace")
        recs = records_for_page(md, version, variant, url_path, args.max_level)
        if not recs:
            continue
        pages += 1
        all_records.extend(recs)
        (cat, _lbl), seg = categorise(url_path)
        cat_stats[cat] = cat_stats.get(cat, 0) + len(recs)
        if cat == "other":
            other_segments[seg] = other_segments.get(seg, 0) + 1
        key = f"{version}/{variant}"
        slice_stats = per_slice.setdefault(key, {"pages": 0, "records": 0})
        slice_stats["pages"] += 1
        slice_stats["records"] += len(recs)

    Path(args.out).write_text(json.dumps(all_records, indent=None), encoding="utf-8")

    if not args.quiet:
        print(f"pages:   {pages}")
        print(f"records: {len(all_records)}")
        if pages:
            print(f"ratio:   {len(all_records) / pages:.2f} records/page")
        print()
        for key in sorted(per_slice):
            st = per_slice[key]
            print(f"  {key:<24} {st['pages']:>6} pages  {st['records']:>7} records")
        print("\ncategory distribution (exhaustive -- every record has one):")
        for cat in sorted(cat_stats, key=lambda c: -cat_stats[c]):
            print(f"  {cat:<14} {cat_stats[cat]:>7} records")
        if other_segments:
            print("\ntop-level sections landing in 'other':")
            for seg in sorted(other_segments, key=lambda s2: -other_segments[s2]):
                print(f"  {seg or '<root>':<24} {other_segments[seg]:>5} pages")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
