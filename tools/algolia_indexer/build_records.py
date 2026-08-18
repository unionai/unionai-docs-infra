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
import math
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
    # Keeps the `other` BUCKET -- the judged-query sets and eval groups are keyed
    # on the category, and moving release notes out of it would silently shift
    # every per-group score. Only the display label changes, so the results list
    # heads these with "Release notes" instead of the catch-all "Other".
    "release-notes": ("other", "Release notes"),
}
CATEGORY_FALLBACK = ("other", "Other")

# ---------------------------------------------------------------- pageRank --
# `weight.pageRank` is the FIRST customRanking criterion, and `custom` is LAST
# in the ranking array -- so this is a pure tie-break between records that
# already tie on every textual criterion. It cannot promote a bad match over a
# good one, and only the ORDER of the values matters, not their magnitude.
# That is why this is deliberately coarse: extra precision buys nothing and
# makes a surprising result harder to explain.
#
# It was previously hardcoded to 0, which made the primary tie-break inert.
#
# Section bases are ordered by MEASURED clicks (90 days, prod analytics), not
# by intuition. Recording the counts because the ordering is the surprising
# part: api-reference earns 28% of all clicks and an earlier hand-tuned pass
# had put it in the BOTTOM bucket.
SECTION_RANK = {
    "user-guide": 30,      # 5224 clicks
    "api-reference": 22,   # 2258
    "tutorials": 15,       # 121
    "security": 15,        # 116
    "deployment": 15,      # 110
    "community": 15,       # 109
    "integrations": 10,    # 38
    "release-notes": 10,   # 11
}
SECTION_RANK_FALLBACK = 12
MAX_DEPTH_BONUS = 6
POPULARITY_SCALE = 10


def page_rank(url_path, popularity):
    """Canonicality prior for one page, shared by all of its records.

    Three terms, in decreasing confidence:
      section    which part of the docs it lives in
      depth      shallower pages are landing/canonical, deeper ones are leaves
      clicks     log-scaled observed popularity, 0 when unknown

    `popularity` is keyed by version- and variant-agnostic path, so the same
    page carries the same prior across v2/latest/pins and across union/flyte.
    That is intentional: they are the same content at different pins.

    Popularity is a WEAK term on purpose. Click data comes from the incumbent
    search, so pages are popular partly BECAUSE the old ranker surfaced them.
    Weighting it heavily would bake that bias in permanently.
    """
    # url_path is "<version>/<variant>/<section>/..." -- the same shape
    # categorise() relies on when it reads parts[2]. Strip that prefix so the
    # section, the depth and the popularity key are all version/variant-free.
    segments = [s for s in url_path.split("/") if s][2:]
    section = segments[0] if segments else ""
    base = SECTION_RANK.get(section, SECTION_RANK_FALLBACK)
    depth_bonus = max(0, MAX_DEPTH_BONUS - len(segments)) * 2
    clicks = popularity.get("/".join(segments), 0) if popularity else 0
    return base + depth_bonus + int(POPULARITY_SCALE * math.log2(1 + clicks))


def load_popularity(path):
    """Load {path: clicks}, or an empty dict when absent.

    Absent is a normal state, not an error: a fresh checkout, a fork, or CI
    before the first refresh. The prior then falls back to section+depth, which
    is still far better than the constant 0 this replaced.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k.strip("/"): v for k, v in data.items() if isinstance(v, (int, float))}


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
ALGOLIA_RECORD_LIMIT = 10000

# Pins are indexed at the SAME granularity as latest and stable. They were held
# at page level; both premises for that have since stopped being true.
#
# The cost argument was "cuts land every ~1.3 days, so indexing every served pin
# at full granularity crosses Grow's 100K allowance inside a week and then grows
# WITHOUT BOUND". Unbounded growth is what made it decisive, and it no longer
# happens: --keep-pins caps the set at the six newest, and DOC-1441 made an
# aged-out pin actually leave the index rather than linger forever. The cost is
# now bounded -- ~10.5K records per pin, six pins, and the seventh evicts the
# first.
#
# The reader argument was "a reader on a pinned snapshot needs to FIND the page;
# deep-linking an h4 in an old copy is a luxury". That held while DOC-1408 sent
# every pin's reader to their line's STABLE, so these records were nearly
# unreachable. Pins are now searched from their own facet, so the reader sees
# page-level hits where a reader on stable sees heading-level ones -- the same
# query answered worse, purely because of which tree they are on.
#
# --keep-pins remains the cost lever, and it is the honest one: fewer whole
# trees, rather than every tree degraded.
PIN_RE = re.compile(r"^v\d+\.\d+")          # v2.5.16.3 -- a pin, not `v2`/`v1`/`latest`


def is_pin(version):
    return bool(PIN_RE.match(version))


def chunk_content(text, limit=MAX_CONTENT_BYTES):
    """Split prose into <=limit-byte pieces on sentence/word boundaries."""
    if not text:
        return []
    if len(text.encode("utf-8")) <= limit:
        return [text]

    def hard_split(s):
        """Split a single oversized run on word boundaries."""
        out, raw = [], s.encode("utf-8")
        while len(raw) > limit:
            cut = raw[:limit].rsplit(b" ", 1)[0] or raw[:limit]
            out.append(cut.decode("utf-8", "ignore"))
            raw = raw[len(cut):].lstrip()
        return out, raw.decode("utf-8", "ignore")

    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate.encode("utf-8")) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # The flushed sentence may itself exceed the limit -- generated API
        # reference prose runs for pages without sentence punctuation, so
        # re-splitting on `.!?` yields one enormous "sentence". Starting a new
        # chunk with it unchecked is how a 13 KB record reached the push and
        # was rejected there instead of here.
        head, current = hard_split(sentence)
        chunks.extend(head)
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


# Emoji shortcodes leading a heading -- `### :sparkles: Task Environment Drawer`,
# the release-notes house style. Hugo renders these (enableEmoji = true in
# hugo.toml), so the PAGE shows a glyph; this tool reads the markdown and never
# runs Hugo, so the literal `:sparkles:` was going into the index and showing up
# in search results as a heading nobody wrote.
#
# Stripped rather than translated: mapping aliases to glyphs needs the gemoji
# dataset, which is one more thing to keep in step with Hugo's, and a wrong
# glyph is worse than none. The emoji carries no retrievable signal either way
# -- readers search the words -- and it is pure noise in the Ask AI corpus,
# where it is spent as tokens the model has to look past.
#
# Only a run at the START of a heading, so nothing mid-text is touched. The
# corpus has `:func:` (a Sphinx role) and `:log-group:` (inside an ARN), and
# both would be eligible under a looser rule; neither ever leads a heading.
# Verified across content/: all 23 heading-leading shortcodes are real gemoji.
# `\s+`, not `\s*`: a shortcode must be followed by space to count, which is
# also Hugo's rule -- `:sparkles:Foo` renders literally there, so leaving it
# literal here is the faithful result, and `:func:`+backtick is untouched even
# when it does lead a heading.
EMOJI_SHORTCODE_RE = re.compile(r"^(?::[a-z0-9][a-z0-9_+-]{1,30}:\s+)+")


def strip_leading_emoji(title):
    """`:sparkles: Foo` -> `Foo`. A title that is ONLY shortcodes is left alone."""
    cleaned = EMOJI_SHORTCODE_RE.sub("", title).strip()
    return cleaned or title


# Inline LINK syntax in a heading. The generated section landing pages are
# link-card indexes, so EVERY one of their headings is `### [Title](url)` --
# and the raw markdown was going into the index as the result title, e.g.
# "[Run scaling](https://www.union.ai/docs/v2/union/user-guide/run-scaling/page.md)".
#
# slugify() already strips this, which is why the ANCHOR was always correct
# (#run-scaling). The two just did not share the logic, so the title kept the
# syntax the anchor had thrown away. Hence clean_title() below, applied at the
# one place titles are produced.
#
# Links only. slugify also drops backticks and emphasis, and neither is safe on
# a title: the modal renders `identifiers` in a hit title as code (codeSpans in
# search.js), and stripping [*_] would turn the API reference's `__init__` into
# `init` and `flyte.*` into `flyte.`.
INLINE_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def clean_title(title):
    """Heading markdown -> the text a reader sees. Never returns empty."""
    cleaned = strip_leading_emoji(INLINE_LINK_RE.sub(r"\1", title).strip())
    return cleaned or title


# A heading that is NOTHING BUT a markdown link is a generated link card, not a
# section. The section landing pages (/user-guide/, /api-reference/, ...) are
# card indexes: each card renders as `### [Child page](url)` plus the child's
# one-line description, so indexing them duplicates every child page's title
# and blurb under the PARENT's url. Searching "run scaling" returned the real
# page and, competing with it, an anchor into the index page that merely links
# there -- strictly the worse destination.
#
# This is the rule SKIP_NAMES already states ("Aggregate files restate page
# content; indexing them duplicates every page's prose under the wrong URL"),
# applied one level down: a landing page is not wholly an aggregate, so it
# cannot be skipped by filename. Its own lead prose -- what Union.ai is, BYOC
# vs Self-managed -- is real content and stays indexed. Only the card sections
# are dropped.
#
# `^[link]$` and nothing else, which is exact on the corpus: NO authored
# heading is a bare link (checked across content/), and the one authored
# heading that CONTAINS a link -- "Abide by the [LF's Code of Conduct](...)" --
# is not one, so it is kept and cleaned by clean_title().
LINK_CARD_HEADING_RE = re.compile(r"^\[[^\]]*\]\([^)]*\)$")

# `## Subpages` is the same scaffolding in its other shape. layouts/_default/
# list.md emits it on every section page, and the llms generator then EXPANDS
# it to carry each child's H2/H3 headings too -- 5 KB on /user-guide/ alone,
# restating the title of every page and heading in the subtree under the
# parent's url. It exists to be traversed by an LLM reading page.md, not read;
# build_llm_docs.py strips it again when it concatenates. Indexed, it means a
# search for any heading anywhere in a subtree can match the parent landing
# page. No authored heading is titled "Subpages" (checked across content/).
SUBPAGES_HEADING_RE = re.compile(r"^subpages$", re.I)


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
            # AFTER the anchor. Hugo derives the heading id from the raw text,
            # before the emoji substitution -- `:sparkles: Task Environment
            # Details Drawer` is served at #sparkles-task-environment-details-
            # drawer. Stripping first would silently break every deep link into
            # the release notes, which is most of what carries these.
            current = {"level": level, "title": clean_title(title),
                       "anchor": anchor, "body": []}
            # Deliberately NOT appended. `current` is still reassigned so the
            # card's body (the child's one-line blurb) lands in a section that
            # is thrown away, rather than being appended to whichever real
            # section happened to precede it. The anchor bookkeeping above has
            # already run, so a later duplicate title still gets Hugo's -1/-2
            # suffix -- the skipped heading occupies an id on the page either way.
            if LINK_CARD_HEADING_RE.match(title) or SUBPAGES_HEADING_RE.match(title):
                continue
            sections.append(current)
        elif current is not None:
            current["body"].append(line)

    return sections


def _version_key(v):
    """Sort pins numerically: v2.5.16.3 -> (2, 5, 16, 3)."""
    return tuple(int(x) for x in re.findall(r"\d+", v))


def prune_slices(dist, dropped):
    """(version, variant) slices whose records must be DELETED from the index.

    A pin outside the --keep-pins window is skipped by the builders, so it never
    reaches records.json. push_records syncs only the slices it finds there, by
    design -- that scoping is what stops main (v2) and the v1 branch wiping each
    other's records. The two rules compose into a leak: a pin that ages out is
    neither rebuilt nor deleted, so its records sit in the index forever,
    pointing at a tree that may no longer be served.

    Reporting it was not enough (the builders already print "outside window, not
    indexed"), because nothing acted on the report. This makes the deletion an
    explicit, narrowly-scoped instruction rather than a widening of the sync
    rule. DOC-1441.
    """
    dropped = set(dropped)
    return sorted((v, variant) for v, variant in _slices(dist) if v in dropped)


def _slices(dist):
    """(version, variant) pairs present in a built dist."""
    root = Path(dist) / "docs"
    return {(p.parts[0], p.parts[1])
            for p in (q.relative_to(root) for q in root.rglob("page.md"))
            if len(p.parts) >= 3}


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


def records_for_page(md, version, variant, url_path, max_level, popularity=None):
    (category, section_label), _seg = categorise(url_path)
    rank = page_rank(url_path, popularity or {})
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
                    "pageRank": rank,
                    "level": 100 - (level * 10),
                    "position": position,
                },
            })

    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist", help="path to the built dist/ tree")
    ap.add_argument("--out", default="records.json")
    ap.add_argument("--prune-out", default=None,
                    help="where to write the list of (version, variant) slices "
                         "that must be DELETED -- pins that have aged out of the "
                         "--keep-pins window. Defaults to <out>.prune.json.")
    ap.add_argument("--max-level", type=int, default=6,
                    help="deepest heading level to index (lower = fewer records)")
    ap.add_argument("--keep-pins", type=int, default=6,
                    help="index only the N newest pinned versions (-1 = all). "
                         "Pins accrue every ~1.3 days and never change, so "
                         "without a window the index grows without bound.")
    ap.add_argument("--popularity",
                    default=str(Path(__file__).with_name("popularity.json")),
                    help="JSON {page path: click count} used as a weak "
                         "popularity prior. Missing file is fine -- the "
                         "prior falls back to section+depth.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.prune_out is None:
        # Append, do NOT use with_suffix(""): the Makefile passes an mktemp name
        # like search-records.ABC123, whose random tail Path treats as a suffix.
        # Stripping it would make the builder write a path the pusher never reads.
        args.prune_out = args.out + ".prune.json"
    popularity = load_popularity(args.popularity)

    pins = sorted({v for v, _ in _slices(args.dist) if is_pin(v)},
                  key=_version_key, reverse=True)
    keep_pins = set(pins if args.keep_pins < 0 else pins[: args.keep_pins])
    dropped = [p for p in pins if p not in keep_pins]

    all_records = []
    pages = 0
    per_slice = {}
    cat_stats = {}
    other_segments = {}

    for path, version, variant, url_path in iter_pages(args.dist):
        if is_pin(version) and version not in keep_pins:
            continue
        md = path.read_text(encoding="utf-8", errors="replace")
        level = args.max_level
        recs = records_for_page(md, version, variant, url_path, level, popularity)
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

    oversized = [r for r in all_records
                 if len(json.dumps(r).encode("utf-8")) > ALGOLIA_RECORD_LIMIT]
    if oversized:
        worst = max(len(json.dumps(r).encode()) for r in oversized)
        raise SystemExit(
            f"ERROR: {len(oversized)} record(s) exceed Algolia's "
            f"{ALGOLIA_RECORD_LIMIT} byte limit (largest {worst}).\n"
            f"       First: {oversized[0]['url']}\n"
            "       Fix the chunker rather than pushing -- a mid-push rejection "
            "leaves the index partially written.")

    Path(args.out).write_text(json.dumps(all_records, indent=None), encoding="utf-8")

    # Sidecar rather than a new key in records.json: that file is a plain list of
    # records and other tools read it as one. Always written, even when empty, so
    # a missing file means "this build did not run the prune logic" rather than
    # "nothing to prune" -- the two need to stay distinguishable.
    prune = prune_slices(args.dist, dropped)
    Path(args.prune_out).write_text(
        json.dumps([{"version": v, "variant": var} for v, var in prune], indent=None),
        encoding="utf-8")

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
        if pins:
            print(f"\npins: {len(keep_pins)} of {len(pins)} indexed, at the same "
                  f"granularity as latest/stable (--keep-pins {args.keep_pins})")
            if dropped:
                print(f"  outside window, not indexed: {', '.join(dropped)}")
                print(f"  -> {len(prune)} slice(s) queued for deletion in "
                      f"{args.prune_out}")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
