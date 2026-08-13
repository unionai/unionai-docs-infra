#!/usr/bin/env python3
"""Generate the Ask AI retrieval index from the built docs.

A SECOND index, alongside the keyword index built by build_records.py, from the
same dist/**/page.md source. The two differ in chunking and schema because they
serve different consumers:

    build_records.py  ->  `union`            keyword search, read by DocSearch
    this script       ->  `union-markdown`   retrieval, read by the Ask AI agent

WHY A SEPARATE INDEX AT ALL. Algolia's own guidance is that Ask AI answers
improve when it reads records "optimized for LLMs" rather than records shaped
for keyword hits. Their stated reason -- stripping navigation and layout
artifacts out of crawled HTML -- does NOT apply here: we never crawl, we read
the served markdown artifact, so our text is already clean. What remains is
CHUNK SHAPE, and that difference is real:

  * the keyword index splits per heading anchor, so a hit can deep-link to the
    exact section. Good for "jump me to the right place".
  * a model answering a question wants the whole explanation in one record.
    Anchor-level chunks fragment an answer across several retrievals and lose
    the surrounding context that made the section make sense.

So this index is chunked PER PAGE, splitting only when a page exceeds the
record limit -- and splitting on heading boundaries when it must, never
mid-sentence.

SIZE. Page-level chunking is why this is cheap: ~4,900 pages across all eight
slices versus ~46,500 anchor-level records. An earlier attempt at an Ask AI
index came out the same size as the keyword index, which was the tell that it
had been duplicated rather than re-chunked -- same records, no benefit.

FACETS. `version` and `variant` are faceted so the client can scope retrieval
per request (`filters: "version:v2 AND variant:union"`). That scoping is what
prevents a reader on a v2 page being answered from v1 content, and it can only
come from the caller -- no agent-side setting can vary per reader.

SETTINGS below are the source of truth; `settings.markdown.json` beside this
file is GENERATED from them via --settings-out and is what `make
index-search-settings` pushes. Change SETTINGS, regenerate, commit both.

Usage:
    build_markdown_records.py --dist dist --out markdown-records.json
    build_markdown_records.py --dist dist --out /dev/null \\
        --settings-out settings.markdown.json      # regenerate the artifact
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_records import (  # noqa: E402  (path shim must precede the import)
    ALGOLIA_RECORD_LIMIT,
    DOCS_BASE,
    _version_key,
    _slices,
    categorise,
    chunk_content,
    is_pin,
    iter_pages,
    parse_sections,
    strip_markdown,
)

# Well under Algolia's 10 KB hard limit, leaving room for the metadata fields.
# Larger than the keyword index's 7 KB because whole-page records are the point:
# Algolia's guidance is that fragmented answers mean chunks are too small.
MAX_TEXT_BYTES = 8000


def _size(text):
    return len(text.encode("utf-8"))


def split_on_headings(sections, limit=MAX_TEXT_BYTES):
    """Group whole sections into <=limit-byte parts, never splitting a section.

    A page becomes one part unless it is too large, in which case it breaks at
    heading boundaries -- so every part is still a coherent run of prose rather
    than an arbitrary byte slice.

    A SINGLE section can still exceed the limit on its own (generated API
    reference pages have 28 KB sections). Those fall back to build_records'
    chunk_content, which splits on sentence then word boundaries. Emitting them
    whole trips the record-size guard: 18 pages hit exactly that.
    """
    parts, current, current_size = [], [], 0
    for sec in sections:
        body = strip_markdown("\n".join(sec["body"])).strip()
        block = f"{sec['title']}\n{body}".strip() if sec["title"] else body
        if not block:
            continue
        size = _size(block)
        if size > limit:
            # Oversized single section: flush what we have, then split it.
            if current:
                parts.append("\n\n".join(current))
                current, current_size = [], 0
            parts.extend(chunk_content(block, limit))
            continue
        if current and current_size + size > limit:
            parts.append("\n\n".join(current))
            current, current_size = [], 0
        current.append(block)
        current_size += size
    if current:
        parts.append("\n\n".join(current))
    return parts


def records_for_page(md, version, variant, url_path):
    sections = parse_sections(md)
    if not sections:
        return []

    (category, _label), _seg = categorise(url_path)
    title = sections[0]["title"] if sections[0]["level"] == 1 else url_path.split("/")[-1]
    url = f"{DOCS_BASE}/{url_path}/"
    parts = split_on_headings(sections)

    records = []
    for n, text in enumerate(parts):
        oid = url if n == 0 else f"{url}::{n}"
        records.append({
            "objectID": hashlib.sha1(oid.encode()).hexdigest(),
            "url": url,
            "title": title,
            "text": text,
            # part/parts let the model see that a page was split, so it can tell
            # "this is all of it" from "this is one piece of a longer page".
            "part": n,
            "parts": len(parts),
            "version": version,
            "variant": variant,
            "category": category,
        })
    return records


SETTINGS = {
    # version/variant are the scoping facets the client filters on per request.
    # category is the only facet the agent itself is allowed to use.
    "attributesForFaceting": ["filterOnly(version)", "filterOnly(variant)", "category"],
    "searchableAttributes": ["title", "text"],
    "attributesToRetrieve": ["url", "title", "text", "part", "parts",
                             "version", "variant", "category"],
    # No attributeForDistinct: in this index a record IS the retrieval unit, so
    # collapsing by page would hide the later parts of a long page.
    "attributesToSnippet": [],
    "advancedSyntax": True,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--out", default="markdown-records.json")
    ap.add_argument("--settings-out", default=None,
                    help="also write the index settings JSON here")
    ap.add_argument("--keep-pins", type=int, default=6,
                    help="index the N newest pinned versions. Matches "
                         "build_records' default so Ask AI has the SAME scoping "
                         "granularity as keyword search: a reader on a pinned "
                         "tree is answered from that pin, not from its line's "
                         "current docs. Pins accrue every ~1.3 days, hence the "
                         "retention window.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    pins = sorted({v for v, _ in _slices(args.dist) if is_pin(v)},
                  key=_version_key, reverse=True)
    keep = set(pins if args.keep_pins < 0 else pins[: args.keep_pins])
    skipped_pins = [p for p in pins if p not in keep]

    records, pages, per_slice = [], 0, {}
    for path, version, variant, url_path in iter_pages(args.dist):
        if is_pin(version) and version not in keep:
            continue
        recs = records_for_page(path.read_text(encoding="utf-8", errors="replace"),
                                version, variant, url_path)
        if not recs:
            continue
        pages += 1
        records.extend(recs)
        st = per_slice.setdefault(f"{version}/{variant}", {"pages": 0, "records": 0})
        st["pages"] += 1
        st["records"] += len(recs)

    oversized = [r for r in records
                 if len(json.dumps(r).encode("utf-8")) > ALGOLIA_RECORD_LIMIT]
    if oversized:
        worst = max(len(json.dumps(r).encode()) for r in oversized)
        raise SystemExit(
            f"ERROR: {len(oversized)} record(s) exceed Algolia's "
            f"{ALGOLIA_RECORD_LIMIT} byte limit (largest {worst}).\n"
            f"       First: {oversized[0]['url']}\n"
            "       Lower MAX_TEXT_BYTES rather than pushing -- a mid-push "
            "rejection leaves the index partially written.")

    Path(args.out).write_text(json.dumps(records, indent=None), encoding="utf-8")
    if args.settings_out:
        Path(args.settings_out).write_text(json.dumps(SETTINGS, indent=2) + "\n",
                                           encoding="utf-8")

    if not args.quiet:
        print(f"pages:   {pages}")
        print(f"records: {len(records)}")
        if pages:
            print(f"ratio:   {len(records) / pages:.2f} records/page "
                  f"(1.00 = one record per page)")
        print()
        for key in sorted(per_slice):
            st = per_slice[key]
            print(f"  {key:<24} {st['pages']:>6} pages  {st['records']:>7} records")
        if skipped_pins:
            print(f"\npins not indexed ({len(skipped_pins)}): "
                  f"{', '.join(skipped_pins[:6])}"
                  f"{' …' if len(skipped_pins) > 6 else ''}")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
