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

Each level falls back to its `union` user guide, **excluding the real variants** (verified: v1 and v2 each have only `flyte` + `union` as live content; `byoc`/`serverless`/`selfmanaged` are stubs). All three: **Static redirect, 302, preserve query string ON**, placed **after** rules #4–7.

**F1** → `https://www.union.ai/docs/v1/union/user-guide/`
```
(http.host eq "www.union.ai" and (http.request.uri.path eq "/docs/v1" or starts_with(http.request.uri.path, "/docs/v1/"))
 and not (http.request.uri.path eq "/docs/v1/flyte" or starts_with(http.request.uri.path, "/docs/v1/flyte/"))
 and not (http.request.uri.path eq "/docs/v1/union" or starts_with(http.request.uri.path, "/docs/v1/union/")))
```

**F2** → `https://www.union.ai/docs/v2/union/user-guide/`
```
(http.host eq "www.union.ai" and (http.request.uri.path eq "/docs/v2" or starts_with(http.request.uri.path, "/docs/v2/"))
 and not (http.request.uri.path eq "/docs/v2/flyte" or starts_with(http.request.uri.path, "/docs/v2/flyte/"))
 and not (http.request.uri.path eq "/docs/v2/union" or starts_with(http.request.uri.path, "/docs/v2/union/")))
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
