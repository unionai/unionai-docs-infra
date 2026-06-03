# Hand-off: fix the `www.union.ai/docs/` redirect (query strings + path suffixes)

**Audience:** whoever owns the Cloudflare dashboard for the `union.ai` zone (Union Systems Inc. account).
**Status:** rule specs below are ready to apply. The companion `redirects.csv` change in this PR fixes a related production bug (see Rule C).
**Context:** see [`ROUTING-ARCHITECTURE.md`](./ROUTING-ARCHITECTURE.md) Phase 2 ("Redirect Rules"). These are Cloudflare **Dynamic Redirect** rules, configured in the dashboard — not in this repo.

## The bug

The docs-root redirect chain is:

```
/docs        →308→ /docs/                    (trailing-slash normalize)
/docs/       →302→ /docs/v2/                 (Redirect Rule #3)   ← BROKEN
/docs/v2/    →302→ /docs/v2/union/           (default-variant rule)
/docs/v2/union/ → … → user-guide            (Hugo landing)
```

**Redirect Rule #3 matches on the full request URI (`http.request.uri`), exactly `/docs/`.** Anything that isn't *exactly* `/docs/` misses the rule, falls through to CloudFront's `/docs/*` behavior → v2 Cloudflare Pages origin, which serves its **root `index.html` landing stub (≈791 bytes) with HTTP 200** — a soft-404. Two symptoms:

| Request | Today | Why |
|---|---|---|
| `/docs/` | 302 → `/docs/v2/` ✓ | exact URI match |
| `/docs/?utm_source=x` | **200, soft-404 stub** ✗ | query string makes the URI ≠ `/docs/`, so the rule misses |
| `/docs/getting-started` | **200, soft-404 stub** ✗ | path suffix makes the URI ≠ `/docs/`, and no catch-all exists |

The sibling `/docs/v2/ → /docs/v2/union/` rule is written correctly (matches on `.path`, preserves the query string) and works — it's the model to copy. Real content pages serve fine *with* query strings (verified: `…/user-guide/?utm_source=google` → 200), so the bug is isolated to the redirect rule, not page serving.

## The fixes (3 Cloudflare Dynamic Redirect rules)

All rules: **302**, **"Preserve query string" = ON**.

### A — Fix Rule #3 (the query-string bug)

Change the match from a full-URI exact match to a **path** match:

- **Match:** `http.host eq "www.union.ai" and http.request.uri.path eq "/docs/"`
- **Target (dynamic / static):** `https://www.union.ai/docs/v2/`
- **Preserve query string:** ON

After this, `/docs/?utm_source=…` redirects correctly with the query preserved.

### B — Add a catch-all for unknown `/docs/<foo>`

Sends any unversioned, unrecognized `/docs/*` path to the docs home (same destination the root resolves to). Exclusions keep it from swallowing real content or the prefixes handled by other rules.

- **Match:**
  ```
  http.host eq "www.union.ai"
    and starts_with(http.request.uri.path, "/docs/")
    and not starts_with(http.request.uri.path, "/docs/v1/")
    and not starts_with(http.request.uri.path, "/docs/v2/")
    and not starts_with(http.request.uri.path, "/docs/byoc/")
    and not starts_with(http.request.uri.path, "/docs/serverless/")
    and not starts_with(http.request.uri.path, "/docs/flyte/")
    and not starts_with(http.request.uri.path, "/docs/selfmanaged/")
    and not starts_with(http.request.uri.path, "/docs/union/")
    and not starts_with(http.request.uri.path, "/_static/")
  ```
- **Target:** `https://www.union.ai/docs/v2/union/`
- **Preserve query string:** ON
- **Ordering:** place after the specific `/docs/{variant}/*` rules (#4–7). The exclusions make it order-independent anyway.

> **Trade-off:** redirecting unknown paths to the home is strictly better than today's soft-404-with-200, but the *most correct* behavior for genuinely-dead URLs is a real **hard 404** (serve Hugo's `404.html` with a 404 status at the Pages layer). Recommend shipping B now and treating hard-404 as a follow-up.

### C — Add the missing unversioned `union` → v2 rule

The existing rules #4–7 map unversioned `byoc`/`serverless`/`flyte`/`selfmanaged` → **v1**. There is **no rule for unversioned `union`** (a v2-only variant), so `www.union.ai/docs/union/*` soft-404s. ~371 legacy `docs.union.ai/*` bulk-redirect targets land there and are **dead in production today** (e.g. `docs.union.ai/building-workflows/launch-plans` → `/docs/union/user-guide/core-concepts/launch-plans` → 200 stub).

- **Match (wildcard):** source `https://www.union.ai/docs/union/*`
- **Target (wildcard):** `https://www.union.ai/docs/v2/union/${1}`
- **Preserve query string:** ON

> The `redirects.csv` change in this PR rewrites those ~371 targets to `/docs/v2/union/…` directly (no extra hop), so the CSV no longer *depends* on Rule C. Rule C is still worth adding to catch out-of-CSV / hand-typed / externally-linked `/docs/union/…` URLs.

## Recommended order

1. **A** — the actual query-string fix; one-line expression change, lowest risk.
2. **C** + this PR's `redirects.csv` change — repairs ~371 dead legacy redirects.
3. **B** — closes the general `/docs/<foo>` soft-404 gap.

## Verification (after applying)

```bash
curl -sSI "https://www.union.ai/docs/?utm_source=x"        # expect 302 → /docs/v2/?utm_source=x   (A)
curl -sSI "https://www.union.ai/docs/getting-started"      # expect 302 → /docs/v2/union/           (B)
curl -sSIL "https://www.union.ai/docs/union/user-guide"    # expect → /docs/v2/union/user-guide 200 (C)
curl -sSIL "https://docs.union.ai/building-workflows/launch-plans"  # expect a real page, not the 791-byte stub
```
