# Algolia indexer

Generates the on-site search index from the **built docs** rather than by
crawling the published site, and pushes it to Algolia.

```
build_records.py            dist/**/<path>.md ->  records.json      (keyword)
build_markdown_records.py   dist/**/<path>.md ->  md-records.json   (retrieval)
push_records.py             either records file ->  Algolia (scoped per line)
build_synonyms.py           migration tables  ->  synonyms.draft.json
settings.json               index settings for `union`
settings.markdown.json      index settings for `union-markdown`
ask-ai-prompt.md            the Ask AI agent's system prompt
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

`version` and `variant` are what the frontend filters on, and what Ask AI must be given too --
see below. On `union` they are ordinary facets (`facetFilters`); on `union-markdown` they are
declared `filterOnly`, and Ask AI passes them as a `filters` string.

## Two indexes

| index | built by | contents | consumer |
|---|---|---|---|
| `union` | `build_records.py` | every version and variant, chunked **per heading anchor** | site search (DocSearch-style), faceted client-side |
| `union-markdown` | `build_markdown_records.py` | every version and variant, chunked **per page** | the Ask AI agent |

Both live in Algolia application `42EK9RXSGL` and both are pushed by `make index-search`.
`--keep-pins` defaults to 6 in *both* builders, deliberately, so Ask AI retrieves over the same
set of trees that search covers.

**Scoping is at query time, not by index.** Ask AI is pointed at the full `union-markdown`
index and passed the reading page's own facets as a filter string
(`filters: "version:v2 AND variant:union"`); the keyword widget passes the equivalent
`facetFilters` array. `union-markdown` declares `version` and `variant` as `filterOnly` facets
for exactly this. Neither index is restricted to a single line at build time.

> **Historical note.** An earlier design built a separate v2-only index called `union_askai`
> via a `--only-version` flag. That index and that flag no longer exist. Query-time scoping
> replaced them, for the reason below.

Query-time scoping is what keeps the modal coherent. Ask AI and search share one box, so with a
fixed-version index the results list would follow the reader while the answer did not — the two
halves describing different products, each lending the other false authority. Scoped at query
time, a v1 reader gets v1 results *and* a v1 answer.

That matters more than staleness: v1 is not merely old for a v2 reader. The SDK was rewritten,
so a v2 answer handed to a v1 reader is wrong, and confidently so. Version is the dangerous
axis — union/flyte differ on feature availability, v1/v2 differ on the whole API surface.

### Why a second index at all

Not for cleanliness of text: we never crawl, we read the served `<path>.md` twin, so the prose is
already free of navigation and layout artifacts. The real difference is **chunk shape**:

- the keyword index splits per heading anchor, so a hit can deep-link to the exact section —
  good for "jump me to the right place";
- a model answering a question wants the whole explanation in one record. Anchor-level chunks
  fragment an answer across several retrievals and lose the context that made the section
  cohere.

So `union-markdown` chunks per page, splitting only when a page exceeds the record limit, and
splitting on heading boundaries when it must — never mid-sentence.

`ask-ai-prompt.md` assumes this scoped-retrieval design and must be rewritten if that assumption
is ever dropped.

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

# keyword index
build_records.py --dist dist --out records.json
push_records.py  --records records.json --index union

# Ask AI retrieval index
build_markdown_records.py --dist dist --out md-records.json
push_records.py  --records md-records.json --index union-markdown

# settings are applied DELIBERATELY, never on a deploy (see the Makefile)
push_records.py --index union           --settings settings.json
push_records.py --index union-markdown  --settings settings.markdown.json
```

Credentials come from the environment, never from arguments:
`ALGOLIA_DOCS_2_APPLICATION_ID`, `ALGOLIA_DOCS_2_WRITE_API_KEY`. Despite the `_2_` name these are
**the live production app** (`42EK9RXSGL`) — the one the site queries.

`ALGOLIA_DOCS_1_*` pointed at the retired Crawler's app, which **has since been deleted**. Those
variables are dead; do not reintroduce a reference to them.
