# Algolia indexer

Generates the on-site search index from the **built docs** rather than by
crawling the published site, and pushes it to Algolia.

```
build_records.py   dist/**/<path>.md ->  records.json
push_records.py    records.json     ->  Algolia (scoped per line)
build_synonyms.py  migration tables ->  synonyms.draft.json
settings.json      index settings for the search index
settings.askai.json               settings for the Ask AI index
ask-ai-prompt.md   the Ask AI agent's system prompt
```

## Why build-time, not crawled

| | hosted Crawler | build-time |
|---|---|---|
| cost | crawl-metered; on-deploy crawling of this corpus ran $134–6,300/mo | no crawl meter |
| `robots: noindex` | crawler obeys it, so `noindex` also removed pages from **site** search | irrelevant — reads `dist/` |
| `variant` / `version` | re-derived from the URL by regex | taken from the build path |
| CDN cache ghosts | retired trees answer at the edge for days after leaving `versions.toml`; a crawler indexes content that then 404s | impossible |

The second row is the one that changed the site's posture: SEO canonicalisation
and site-search membership used to be the same knob. They are now independent —
a page can be `noindex` for Google and fully searchable on-site.

## Index membership follows the build

Whatever `dist/docs/<version>/<variant>/**/<path>.md` contains gets indexed. Not
what `versions.toml` declares, not what the CDN still serves from cache. Served
implies searchable; unserved cuts produce no pages and are skipped by
construction.

The `<path>.md` twin is the **served** markdown artifact, so records carry resolved prose
rather than raw shortcode markup.

## Granularity: full for current surfaces, page-level for pins

A pinned version tree is a near-complete copy of its line. At full anchor
granularity a pin costs **~10.5K records (v2)** or **~23.3K (v1** — its flytekit
reference is heading-dense**)**. Cuts land roughly every **1.3 days**, so
indexing every served pin at full granularity crosses Grow's 100K record
allowance in **under a week**, then grows without bound:

| pins retained (full granularity) | records | $/month |
|---|---|---|
| 1 month | ~519K | ~$168 |
| 3 months | ~1.47M | ~$549 |
| 1 year | ~5.7M | ~$2,260 |

Pins are therefore indexed at **page level** (`PIN_MAX_LEVEL = 1`): ~1.2K
records each, about 9× cheaper. A reader on a pinned historical snapshot needs
to *find the page*; deep-linking a specific `h4` in an old copy is a luxury.
Current surfaces (`latest`, `v2`, `v1`) keep full anchor-level records.

A pin is anything matching `v<major>.<minor>…` — `v2.5.16.3` is a pin, `v2` and
`v1` are line stables and are not.

## Retention window

`--keep-pins N` (default **6**) indexes only the N newest pins and reports the
rest by name. Pins never change and accrue every couple of days, so without a
window the index grows forever. `-1` keeps all.

The tool **names what it dropped**. A retention window that silently discards
content reads as "everything is indexed" when it isn't.

## Per-line scoping — the rule that matters

`main` builds the v2 line; the `v1` branch builds v1. Every write is scoped to
the `(version, variant)` slices present in the records file, and nothing outside
them is touched.

**`replace_all_objects` is deliberately unused.** It replaces the *entire*
index, which is shared by every version and variant — calling it from `main`
would wipe the whole v1 line. The scoped sync **is** the reconciliation: its
delete pass removes records for pages that disappeared.

## Facets

`version`, `variant`, `category`, `type`. `lang` was dropped: it was inherited
verbatim from the crawler's settings, and no record we generate carries it, so
it advertised a filter that could never match.

`version` and `variant` are what the frontend filters on, and what Ask AI must
be given too -- see below.

## Two indexes

| index | contents | consumer |
|---|---|---|
| `union` | every version and variant | site search, faceted client-side |
| `union_askai` | **v2 only** (`--only-version v2`) | Ask AI |

**`union_askai` is probably redundant — prefer scoping at query time.** It was
built on the assumption that Agent Studio could not filter, because its
agent-creation wizard never asks about facets. Algolia have since confirmed it
accepts `searchParameters.facetFilters` per request, and can lock facets in the
Algolia Search tool's `searchControls`.

Pointing the agent at the full `union` index and passing the page's own
`version`/`variant` is strictly better, because Ask AI and search share one
modal. With a fixed-version index the results list follows the reader while the
answer does not, so the two halves of the same box describe different products
and each lends the other false authority. With query-time scoping a v1 reader
gets v1 results *and* a v1 answer.

Either way the point stands: v1 is not merely stale for a v2 reader. The SDK
was rewritten, so a v2 answer given to a v1 reader is wrong, and confidently
so. Version is the dangerous axis — union/flyte differ on feature availability,
v1/v2 differ on the whole API surface.

`ask-ai-prompt.md` assumes the scoped-retrieval design and must be rewritten if
that assumption is ever dropped.

## Traps worth knowing

- **Headings inside fenced code blocks are not headings.** A `# Direct call`
  Python comment would otherwise become a record with an anchor that does not
  exist in the rendered page.
- **Content is chunked, not truncated**, at Algolia's 10 KB record limit.
  Truncating silently makes the tail of a long page unsearchable. Generated
  reference prose can run for pages without sentence punctuation, so the
  chunker must hard-split a "sentence" that is itself oversized.
- **Record size is validated at generation.** A mid-push rejection leaves the
  index partially written and the operator guessing which slices landed.
- **Anchors must match Hugo's heading IDs** or every deep link lands at the top
  of the page. Validated against the `id` attributes in built HTML.
- **Aggregates are skipped** (`_section.md`, `llms.txt`, `llms-full.txt`) — they
  restate page content and would index the same prose under the wrong URL.

## Usage

```bash
# after `make variant` has produced dist/
build_records.py --dist dist --out records.json                 # default window
build_records.py --dist dist --out records.json --keep-pins -1  # every pin

push_records.py --records records.json --index union --settings settings.json
push_records.py --records records.json --index union_askai \
                --settings settings.askai.json --only-version v2
```

Credentials come from the environment, never from arguments:
`ALGOLIA_DOCS_2_APPLICATION_ID`, `ALGOLIA_DOCS_2_WRITE_API_KEY`.
`ALGOLIA_DOCS_1_*` is the old crawled app and is never written to.
