# `www.union.ai/docs/` redirect fixes — as-built record

**Status:** all items below are **deployed/applied in production** unless marked otherwise.
**Context:** see [`ROUTING-ARCHITECTURE.md`](./ROUTING-ARCHITECTURE.md) Phase 2. The Cloudflare rules live in the `union.ai` zone (Union Systems Inc. account) under **Rules → Redirect Rules → Single Redirects** (the API `http_request_dynamic_redirect` phase). The bulk-redirect list is `redirects.csv` in this repo, deployed by `deploy-redirects.yml`.

> **Execution order gotcha:** Cloudflare evaluates **Single (dynamic) Redirects before Bulk Redirects** — verified live. So a dynamic rule that matches a path *shadows* any bulk-list entry for the same path. This is why the dynamic fallback rules below supersede several bulk entries.

## The original bug (fixed)

Redirect Rule #3 matched the **full request URI** (`http.request.uri`), exactly `/docs/`. Any query string or path suffix missed it, fell through to the Pages origin, and got a **soft-404 stub (HTTP 200)**.

**Fix A — applied.** Rule #3 now matches on **path**:
- Match: `http.host eq "www.union.ai" and http.request.uri.path eq "/docs/"`
- Static redirect → `https://www.union.ai/docs/v2/`, 302, **preserve query string ON**

`/docs/?utm_source=x` → `/docs/v2/?utm_source=x` ✓ (query preserved end-to-end).

## Fallback rules F1/F2/F3 — applied

Each level falls back to its `union` user guide. **F1/F2 exclude the real variants AND the legacy `byoc`/`serverless`/`selfmanaged` prefixes** — the latter are excluded so their ~5,500 specific per-page bulk mappings (`/docs/vN/byoc/X` → `/docs/vN/union/X`) take effect instead of being shadowed by the fallback (dynamic redirects run before bulk). Genuinely-unknown paths still hit the fallback. All three: **Static redirect, 302, preserve query string ON**, placed **after** rules #4–7.

> v1/v2 each have only `flyte` + `union` as *live* variants; `byoc`/`serverless`/`selfmanaged` are not real variants, but they have comprehensive bulk redirects pointing at the corresponding `union` pages. Note `/docs/v1/union/` is a **reduced** variant — it's missing whole sections the old structure had (`flytectl-cli`, `flytekit-sdk` extras, all `plugins/*`, `architecture/*`, most `integrations/*`, `tutorials/*`); the bulk entries that pointed at those dead `/docs/v1/union/*` pages were repointed to the v1 user guide (see redirects.csv section).

**F1** → `https://www.union.ai/docs/v1/union/user-guide/`
```
(http.host eq "www.union.ai" and (http.request.uri.path eq "/docs/v1" or starts_with(http.request.uri.path, "/docs/v1/"))
 and not (http.request.uri.path eq "/docs/v1/flyte" or starts_with(http.request.uri.path, "/docs/v1/flyte/"))
 and not (http.request.uri.path eq "/docs/v1/union" or starts_with(http.request.uri.path, "/docs/v1/union/"))
 and not (http.request.uri.path eq "/docs/v1/byoc" or starts_with(http.request.uri.path, "/docs/v1/byoc/"))
 and not (http.request.uri.path eq "/docs/v1/selfmanaged" or starts_with(http.request.uri.path, "/docs/v1/selfmanaged/"))
 and not (http.request.uri.path eq "/docs/v1/serverless" or starts_with(http.request.uri.path, "/docs/v1/serverless/")))
```

**F2** → `https://www.union.ai/docs/v2/union/user-guide/`
```
(http.host eq "www.union.ai" and (http.request.uri.path eq "/docs/v2" or starts_with(http.request.uri.path, "/docs/v2/"))
 and not (http.request.uri.path eq "/docs/v2/flyte" or starts_with(http.request.uri.path, "/docs/v2/flyte/"))
 and not (http.request.uri.path eq "/docs/v2/union" or starts_with(http.request.uri.path, "/docs/v2/union/"))
 and not (http.request.uri.path eq "/docs/v2/byoc" or starts_with(http.request.uri.path, "/docs/v2/byoc/"))
 and not (http.request.uri.path eq "/docs/v2/selfmanaged" or starts_with(http.request.uri.path, "/docs/v2/selfmanaged/"))
 and not (http.request.uri.path eq "/docs/v2/serverless" or starts_with(http.request.uri.path, "/docs/v2/serverless/")))
```

**F3** (must be after #4–7) → `https://www.union.ai/docs/v2/union/user-guide/`
```
(http.host eq "www.union.ai" and (http.request.uri.path eq "/docs" or starts_with(http.request.uri.path, "/docs/"))
 and not (http.request.uri.path eq "/docs/v1" or starts_with(http.request.uri.path, "/docs/v1/"))
 and not (http.request.uri.path eq "/docs/v2" or starts_with(http.request.uri.path, "/docs/v2/")))
```

Net behavior:
- `/docs/<foo≠v1,v2>` and `/docs`, `/docs/` → `…/docs/v2/union/user-guide/`
- `/docs/v2/<foo≠flyte,union>` and `/docs/v2/`, `/docs/v2` → `…/docs/v2/union/user-guide/`
- `/docs/v1/<foo≠flyte,union>` and `/docs/v1/`, `/docs/v1` → `…/docs/v1/union/user-guide/`
- Real content (`/docs/{v1,v2}/{flyte,union}/…`) is excluded → served normally
- Unversioned `/docs/flyte/*` still → `/docs/v1/flyte/*` (rule #6, real content) because F3 sits after #4–7

## Existing rules #4–7 (unchanged)

`/docs/{byoc,serverless,flyte,selfmanaged}/*` → `/docs/v1/{…}/*`. Only `flyte` there is real content; the other three land on v1 stubs. Left as-is (not in scope for this work). To route `byoc/serverless/selfmanaged` to a user guide instead, disable #4/#5/#7 and let F3 catch them.

## Bulk-redirect (`redirects.csv`) changes — deployed

- **Version-prefix fix** (#120): legacy `docs.union.ai/*` targets repointed from unversioned `/docs/union/…` → `/docs/v2/union/…`.
- **Remap dead targets** (#121): ~440 stale targets repointed to curl-verified live v2 pages (the v2 IA reorganized). 12 residual generated/ambiguous targets left as honest 404s — see #121.
- **C** (`/docs/union/* → /docs/v2/union/*`, bulk subpath) was shipped (#122) then **superseded by F3** and **removed** — F3 sends `/docs/union/*` straight to the user guide (always a live page; C's per-path targets often 404'd).
- **Removed** the now-dead `/docs/v2`, `/docs/v2/` → `/docs/v2/union/` bulk rows (superseded by F2).
- **Repointed dead targets → user guide:** when `byoc/serverless/selfmanaged` were excluded from F1/F2 (so their specific bulk mappings fire), 131 bulk entries still pointed at `/docs/v{1,2}/union/*` pages that 404 (12 in v2; 119 in v1 — the reduced-variant gap). Those were repointed to the matching version's `…/union/user-guide/`.
- **Subpath prefix-swap for the variant roots:** added 6 `subpath_matching` + `preserve_path_suffix` entries — `www.union.ai/docs/v{1,2}/{byoc,selfmanaged,serverless}` → `https://www.union.ai/docs/v{1,2}/union` — replacing the old no-slash-only bare entries. Cloudflare bulk redirects match longest-source-first, so the ~5,500 specific deep entries still win; this broad entry catches the bare root (`/docs/v2/byoc/` → `/docs/v2/union/`) and any *uncovered* deep path (`/docs/v2/byoc/<x>` → `/docs/v2/union/<x>`). Since `union` is a real variant, an uncovered path then hits the per-variant nearest-page 404 — closing the soft-stub gap. Net: every `/docs/v{1,2}/{byoc,serverless,selfmanaged}/*` URL lands on its specific page, the union equivalent, or the nearest-page 404 — never the static stub.

## Trailing-slash + unknown-slug normalization on `docs.union.ai` (DOC-1218)

**Status: pending** — draft PR on `docsy/docsunionai-slash-redirect-infra`; deploys when the `unionai-docs` submodule pointer is bumped (`deploy-redirects.yml`). Tracks [DOC-1218](https://linear.app/unionai/issue/DOC-1218).

### The bug
The 306 legacy `docs.union.ai/*` bulk rows used `subpath_matching=FALSE` (exact match). A request with a **trailing slash** (`docs.union.ai/administration/`) or an **unknown slug** (`docs.union.ai/bogusxyz123`) missed every exact source, fell through to the v2 Pages origin, and got the **793-byte index stub at HTTP 200** — the same soft-404 family as [the original bug](#the-original-bug-fixed) above. (Query strings already survived — `preserve_query=TRUE`; the discriminator was the trailing slash.)

### The fix (CSV-only)
- **Flipped `subpath_matching` FALSE→TRUE on all 306 `docs.union.ai` rows** (`preserve_path_suffix` was already TRUE). A trailing slash now matches the row as a subpath and the `/` suffix is appended → the canonical page: `docs.union.ai/administration/` → `…/user-guide/user-management/`.
- **Retargeted the bare-root `docs.union.ai` row** to `https://www.union.ai/docs/v2/union/user-guide/` with `preserve_path_suffix=FALSE`. By Cloudflare longest-source-first matching it is the lowest-priority fallback (every specific row wins), so it only catches **unknown** slugs → user guide — and `preserve_path_suffix=FALSE` drops the garbage suffix so they land on the guide itself, not `…/user-guide/<garbage>` (404).

Same pattern already proven by the variant-root rows (`www.union.ai/docs/v{1,2}/{byoc,…}`, subpath + preserve_path_suffix) — 1,526 `subpath=TRUE` rows run in prod. Verified live: `/docs/v2/byoc/` → `/docs/v2/union/` and `/docs/v2/byoc/deep/madeup/?utm=x` → `/docs/v2/union/deep/madeup/?utm=x`.

### Caveat (accepted)
A **made-up deep subpath under a known prefix** (e.g. `docs.union.ai/administration/xyz`) now 302→404 (`…/user-guide/user-management/xyz`) instead of 200-stub — lateral, and only for invented URLs. Acceptable for a sunset host.

### Verification (after deploy)
```bash
curl -sSI "https://docs.union.ai/administration"          # 301 → …/user-guide/user-management        (exact, unchanged)
curl -sSI "https://docs.union.ai/administration/"         # 301 → …/user-guide/user-management/        (trailing slash — FIXED)
curl -sSI "https://docs.union.ai/getting-started/?utm=x"  # 301 → …/user-guide/?utm=x                  (slash + query preserved)
curl -sSI "https://docs.union.ai/bogusxyz123"             # 302 → …/docs/v2/union/user-guide/          (unknown slug — FIXED, was 200 stub)
```

## Parked

**Hard-404 for genuinely-missing `/docs/<foo>`** (originally "Rule B", reconsidered as a Pages-level fix): the build only emits `404.html` under `dist/docs/<version>/<variant>/`, so paths above the variant level fall back to the index stub (200) instead of a real 404. A fix would add a root `dist/404.html` (or flip the Pages project's not-found handling) — but the build is shared with PR previews, which have no CF redirect layer and rely on the stub as their entry point. The F1/F2/F3 fallbacks now redirect those paths to a user guide instead, so this is parked.

## Verification

```bash
curl -sSIL "https://www.union.ai/docs/?utm_source=x"        # → /docs/v2/union/user-guide/?utm_source=x   (A + F2)
curl -sSI  "https://www.union.ai/docs/bogusxyz"             # 302 → /docs/v2/union/user-guide/             (F3)
curl -sSI  "https://www.union.ai/docs/v2/byoc"              # 302 → /docs/v2/union/user-guide/             (F2)
curl -sSI  "https://www.union.ai/docs/v1/bogus"             # 302 → /docs/v1/union/user-guide/             (F1)
curl -sSI  "https://www.union.ai/docs/v2/flyte/"            # 200 (excluded — real content, not redirected)
curl -sSI  "https://www.union.ai/docs/flyte/foo"            # 302 → /docs/v1/flyte/foo (rule #6, still real)
```
