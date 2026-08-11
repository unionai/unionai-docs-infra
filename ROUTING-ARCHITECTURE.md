# Union.ai Web Routing Architecture

This document describes the complete request routing system that serves `union.ai`, `www.union.ai`, `docs.flyte.org`, `docs-legacy.flyte.org`, and related domains. The system spans three services: Cloudflare (DNS + rules + static hosting), AWS CloudFront (path-based reverse proxy), and Webflow (corporate site).

## Overview

The public-facing domain is `union.ai` (and `www.union.ai`). All web traffic enters through Cloudflare DNS, passes through AWS CloudFront for path-based routing, and is ultimately served by one of three backends:

| Content | Backend | Platform |
|---------|---------|----------|
| v2 docs (`/docs/v2/`) | web-docs.union.ai | Cloudflare Pages |
| v1 docs (`/docs/v1/`) | v1.docs-dog.pages.dev | Cloudflare Pages |
| Corporate site (everything else) | web.union.ai | Webflow |

The legacy domains `docs.flyte.org` and `docs-legacy.flyte.org` are both handled entirely within Cloudflare (no CloudFront involvement) and redirect all traffic to `www.union.ai/docs/v2/flyte/...` via a shared dual-host rule chain plus the union.ai bulk-redirect list.

Two Cloudflare accounts are involved:

- **Admin@flyte.org's Account** — manages `flyte.org` and `flyte.io` zones
- **Union Systems Inc. Account** — manages `union.ai`, `unionai.com`, `unionai.dev`, and other Union domains

## The Complete Request Flow

### Phase 1: DNS Resolution (Cloudflare)

All DNS is managed in Cloudflare. The key CNAME records (from the exported DNS data):

| Domain | CNAME Target | Proxied | Purpose |
|--------|-------------|---------|---------|
| `union.ai` | `d96tdta1ar9l3.cloudfront.net` | Yes | Main site → CloudFront |
| `www.union.ai` | `d96tdta1ar9l3.cloudfront.net` | Yes | Main site → CloudFront |
| `www-new.union.ai` | `d96tdta1ar9l3.cloudfront.net` | Yes | Migration alias → CloudFront |
| `staging.union.ai` | `d2kimnd3ghtw2w.cloudfront.net` | Yes | Staging → separate CloudFront distribution |
| `web.union.ai` | `cdn.webflow.com` | No | Corporate site → Webflow (not proxied) |
| `web-docs.union.ai` | `docs-dog.pages.dev` | Yes | v2 docs → Cloudflare Pages |
| `docs.union.ai` | `docs-dog.pages.dev` | Yes | Legacy docs subdomain → Cloudflare Pages |
| `docs.flyte.org` | `readthedocs.io` | Yes | Legacy Flyte docs (proxied; Cloudflare rules intercept before it reaches ReadTheDocs) |
| `docs-legacy.flyte.org` | `readthedocs.io` | Yes | Legacy Flyte v1.x Sphinx docs (proxied; same rule chain as `docs.flyte.org`) |
| `signup.union.ai` | `cname.vercel-dns.com` | Yes | Signup → Vercel |
| `sandbox.union.ai` | `union.ai` | Yes | Redirects to signup (via page rule) |
| `flyte.org` | `cdn.webflow.com` | No | Flyte corporate site → Webflow |
| `slack.flyte.org` | `8.8.8.8` (dummy A record) | Yes | Proxied dummy; Cloudflare rules redirect to Slack invite |
| `blog.flyte.org` | `8.8.8.8` (dummy A record) | Yes | Proxied dummy; page rule redirects to flyte.org/blog |

**Important**: Both `docs.flyte.org` and `docs-legacy.flyte.org` still have CNAMEs pointing to `readthedocs.io`, but because the records are **proxied** (orange cloud), Cloudflare intercepts requests and applies its redirect rules before they ever reach ReadTheDocs. The CNAME target is effectively unused. The two hostnames share the same rule chain (described in Phase 5 below) and route to the same `_r_/flyte` bus.

Similarly, `slack.flyte.org`, `blog.flyte.org`, `status.flyte.org`, and `demo.flyte.org` use dummy A records (`8.8.8.8`) with proxying enabled — the actual responses come from Cloudflare redirect rules, not from the dummy IP.

There are **two CloudFront distributions**:

| Distribution ID | Alternate Domain Names | Purpose |
|-----------------|----------------------|---------|
| `d96tdta1ar9l3` | `union.ai`, `www.union.ai`, `www-new.union.ai`, `unionai.com`, `www.unionai.com` | Production site |
| `d2kimnd3ghtw2w` | `staging.union.ai` | Staging site |

### Cloudflare Pages Projects (Union Systems Inc. Account)

| Project | Subdomain | Custom Domains | Production Branch |
|---------|-----------|----------------|-------------------|
| **docs** | `docs-dog.pages.dev` | `docs.union.ai`, `web-docs.union.ai` | `main` |
| **docs-builder** | `docs-builder.pages.dev` | *(none)* | `main` (orphan, see below) |

The **docs** project serves both v2 docs (via `web-docs.union.ai`) and the legacy `docs.union.ai` domain. Each deployment also gets a unique preview URL (e.g., `621e958d.docs-dog.pages.dev`).

> **Build source:** as of 2026-05-13, the `docs` project's builds are produced by **GitHub Actions** (`.github/workflows/build-and-deploy.yml` on `unionai/unionai-docs`, both `main` and `v1` branches) and pushed via `wrangler pages deploy` (Direct Upload mode). CF Pages' native build runner is disabled (`source.config.deployments_enabled: false`). The project's stored `build_config` (`make dist` → `dist/`) and `HUGO_VERSION` env vars are no longer read. Deployment `source` field shows `ad_hoc` (wrangler) rather than `github:push` (CF native).
>
> Verify what's live with: `curl https://www.union.ai/docs/build-info.json` (main) or `curl https://www.union.ai/docs/v1/build-info.json` (v1) — returns `"builder": "github-actions"` plus the GHA run URL of the live deployment.

The v1 docs are served from `v1.docs-dog.pages.dev` — this is a deployment alias within the same **docs** Pages project, not a separate project. Built and deployed by the same GHA workflow on the `v1` branch with `--branch=v1`.

The **docs-builder** project was previously used for preview/staging Git-connected builds. After the 2026-05 GHA-build migration it has no role; it remains in the dashboard as an orphan with auto-deploys disabled (1,714 historical deployments make bulk-delete via API expensive vs. its zero-cost inert state).

The Admin@flyte.org account has **no Cloudflare Pages projects**.

The Admin@flyte.org account has **no Cloudflare Pages projects**.

### Workers Routes

Neither the `union.ai` zone nor the `flyte.org` zone has any Cloudflare Workers routes configured. All request processing is done via redirect rules, transform rules, page rules, and bulk redirects.

### Phase 2: Cloudflare Zone Rules (union.ai)

Before traffic reaches CloudFront, Cloudflare processes its own zone rules for `union.ai`. These rules run at the Cloudflare edge.

#### Redirect Rules (Dynamic Redirects)

Processed in order (11 active as of 2026-07-24):

| # | Match | Target | Code | Purpose |
|---|-------|--------|------|---------|
| 1 | `union.ai` (bare domain) | `https://www.union.ai{path}` | 302 | Canonicalize to www |
| 2 | `https://apps.*` | Strip `apps.` prefix | 301 | Legacy apps subdomain |
| 3 | `www.union.ai/docs/` (bare, with trailing slash) | `/docs/v2/` | 302 | Docs root defaults to v2 |
| 4 | `www.union.ai/docs/byoc/*` | `/docs/v1/byoc/*` | 302 | Unversioned byoc = v1 |
| 5 | `www.union.ai/docs/serverless/*` | `/docs/v1/serverless/*` | 302 | Unversioned serverless = v1 |
| 6 | `www.union.ai/docs/flyte/*` | `/docs/v1/flyte/*` | 302 | Unversioned flyte = v1 |
| 7 | `www.union.ai/docs/selfmanaged/*` | `/docs/v1/selfmanaged/*` | 302 | Unversioned selfmanaged = v1 |
| 8 | `staging.union.ai/try-2.0` | Staging Cloudflare Pages URL | 302 | Staging vanity URL |
| 9 | `www.union.ai` + `/docs/v1` or `/docs/v1/*` (excl. flyte/union/byoc/selfmanaged/serverless) | `/docs/v1/union/user-guide/` | 302 | **F1: v1 fallback** — bare/unknown v1 path → v1 landing |
| 10 | `www.union.ai` + `/docs/v2` or `/docs/v2/*` (excl. flyte/union/byoc/selfmanaged/serverless) | `/docs/v2/union/user-guide/` | 302 | **F2: v2 fallback** — bare/unknown v2 path → v2 landing |

*(One further rule beyond these ten is active but not material to `/docs/*` routing. Plus a **URL Rewrite Rule**, "to public docs url", that rewrites any path containing the token `HhI8nGjMQ1x5SrxSip29` to `/`.)*

**Key behavior**: Any request to `www.union.ai/docs/{variant}/*` without an explicit version prefix (`v1` or `v2`) is redirected to v1. This is because v1 was the original URL structure before versioning was introduced. The bare `/docs/` path, however, redirects to v2 (the current default). The **F1/F2 fallbacks** (rules 9–10) catch a bare or unrecognized version root (`/docs/v2`, `/docs/v2/<unknown>`) and land it on that version's user-guide home instead of a 404.

#### Transform Rules

- URLs containing the string `HhI8nGjMQ1x5SrxSip29` are rewritten to `/`. This appears to be a cleanup rule for a preview/staging URL token.

#### Legacy Page Rules

| Match | Target | Code |
|-------|--------|------|
| `sandbox.union.ai/*` | `https://signup.union.ai` | 302 |
| `docs.union.ai/HhI8nGjMQ1x5SrxSip29/*` | `https://docs.union.ai/*` | 302 |

#### Bulk Redirects (8,142 entries)

A single bulk redirect list named "redirects" containing 8,142 entries (as of 2026-07-24; sourced from `unionai-docs-infra/redirects.csv`), broken down by source prefix:

- **~6,256 entries** from `www.union.ai/*` and `docs.union.ai/*` (legacy categories):
  - `www.union.ai/docs/byoc/*` style mappings (unversioned → versioned)
  - `docs.union.ai/*` mappings to `www.union.ai/docs/...`

- **~1,520 entries** under `www.union.ai/_r_/flyte/*` — the landing zone for the `docs.flyte.org` / `docs-legacy.flyte.org` catch-all (see Phase 5). Sources are of the form:
  - `www.union.ai/_r_/flyte/en/latest/<canonical-path>` (main flyte project)
  - `www.union.ai/_r_/flyte/projects/<subproject>/en/latest/<canonical-path>` (subprojects: flytectl, cookbook, flytekit, flyteidl)
  
  Each entry targets a specific `https://www.union.ai/docs/v2/flyte/<v2-path>` destination. Most entries use `subpath_matching=TRUE` to absorb trailing-slash and minor URL-form variants.

All bulk redirects use 302 (temporary) status codes with query string preservation.

**Note**: The `_r_/flyte/*` portion of this list is the landing zone for the `docs.flyte.org` / `docs-legacy.flyte.org` catch-all (see Phase 5). After the flyte.org-zone normalization rules rewrite a request to `www.union.ai/_r_/flyte/<canonical-path>`, this list maps it to the canonical v2 destination. Sourced from `unionai-docs-infra/redirects.csv` and deployed by `tools/redirect_generator/deploy_redirects.py` via the `unionai-docs/.github/workflows/deploy-redirects.yml` CI workflow (auto-runs when the infra submodule pointer changes on `main` or `v1` branches).

### Phase 3: AWS CloudFront (Path-Based Reverse Proxy)

After Cloudflare DNS resolution and any Cloudflare-level redirects, requests to `www.union.ai` reach the CloudFront distribution. CloudFront acts purely as a **path-based reverse proxy** — it does not cache content (all behaviors use `Managed-CachingDisabled`).

#### Production Distribution: `E6QXJSAGSQIN2`

- **Distribution domain**: `d96tdta1ar9l3.cloudfront.net`
- **Alternate domain names**: `union.ai`, `www.union.ai`, `www-new.union.ai`, `unionai.com`, `www.unionai.com`
- **Description**: union.ai
- **Price class**: Use only North America and Europe
- **Supported HTTP versions**: HTTP/2, HTTP/1.1, HTTP/1.0
- **SSL certificate**: `union.ai` (TLSv1.2_2021)
- **Default root object**: `index.html`
- **Logging**: Off
- **Last modified**: January 30, 2026
- **AWS Account**: `384083793579`
- **Managed via Terraform** — do not modify manually

##### Origins

| Origin Name | Origin Domain | Type |
|-------------|---------------|------|
| docs | web-docs.union.ai | Custom origin |
| v1 | v1.docs-dog.pages.dev | Custom origin |
| web | web.union.ai | Custom origin |

##### Cache Behaviors (evaluated in precedence order)

| Precedence | Path Pattern | Origin | Protocol | Cache | Origin Request Policy |
|------------|-------------|--------|----------|-------|----------------------|
| 0 | `/docs/v1` | v1 | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 1 | `/docs/v1/*` | v1 | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 2 | `/docs/v1.*` | v1 | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 3 | `/docs/*` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 4 | `/docs` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 5 | `*` (default) | web | HTTPS only | Disabled | AllViewerExceptHostHeader |

> **Note (2026-07-24):** a former `/_static/*` → docs behavior (precedence 2) was **retired** when CloudFront `/_static/*` serving was removed (DOC-966 / cloud#16921); the table above reflects the current behavior set.

#### Staging Distribution: `E217EWC0JUDO1U`

- **Distribution domain**: `d2kimnd3ghtw2w.cloudfront.net`
- **Alternate domain names**: `staging.union.ai`
- **Description**: staging.union.ai
- **Price class**: Use only North America and Europe
- **Supported HTTP versions**: HTTP/2, HTTP/1.1, HTTP/1.0
- **SSL certificate**: `staging.union.ai` (TLSv1.2_2021)
- **Default root object**: `index.html`
- **Logging**: Off
- **Last modified**: January 29, 2026
- **AWS Account**: `384083793579`
- **Managed via Terraform** — do not modify manually

##### Origins

| Origin Name | Origin Domain | Type |
|-------------|---------------|------|
| docs | staging.docs-dog.pages.dev | Custom origin |
| v1 | v1.docs-dog.pages.dev | Custom origin |
| web | union-staging.webflow.io | Custom origin |

##### Cache Behaviors (evaluated in precedence order)

| Precedence | Path Pattern | Origin | Protocol | Cache | Origin Request Policy |
|------------|-------------|--------|----------|-------|----------------------|
| 0 | `/docs/v1` | v1 | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 1 | `/docs/v1/*` | v1 | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 2 | `/docs/v1.*` | v1 | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 3 | `/docs/*` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 4 | `/docs` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 5 | `*` (default) | web | HTTPS only | Disabled | AllViewerExceptHostHeader |

> **Note (2026-07-24):** a former `/_static/*` → docs behavior (precedence 2) was **retired** when CloudFront `/_static/*` serving was removed (DOC-966 / cloud#16921); the table above reflects the current behavior set.

#### How Both Distributions Work

The production and staging distributions have **identical behavior rules** (same path patterns, same precedence order) but route to **different origins**:

| Origin | Production | Staging |
|--------|-----------|---------|
| docs (v2) | web-docs.union.ai | staging.docs-dog.pages.dev |
| v1 | v1.docs-dog.pages.dev | v1.docs-dog.pages.dev (shared) |
| web | web.union.ai | union-staging.webflow.io |

The v1 docs origin is the same in both distributions — there is no separate staging deployment for v1 docs.

#### CloudFront Behavior Details

**How matching works**: CloudFront evaluates behaviors in precedence order. The first matching path pattern wins. Key points:

- `/docs/v1`, `/docs/v1/*` and `/docs/v1.*` (precedences 0-2) are checked before `/docs/*`, ensuring v1 docs requests go to the v1 origin, not the v2 docs origin. The `/docs/v1.*` row (added 2026-07-31, DOC-1331) catches the **pinned v1 versions** (`/docs/v1.16.26.0/...`): CloudFront path patterns treat `.` literally and `/docs/v1/*` requires a slash after `v1`, so before this row a dotted pin fell through to the v2 origin and served the landing stub — while the tree itself was built and live on the v1 deployment. The first v1 pin only came into existence with the `v1.16.26.1` cut, which is why the gap surfaced then.
- `/docs` and `/docs/*` catch all remaining docs traffic (v2 and anything else — including the versioned `/docs/latest`, `/docs/stable`, `/docs/v2.x.y.z` paths, see below) and route to the v2 docs origin.
- Everything else hits the default behavior and goes to Webflow.

**AllViewerExceptHostHeader**: This origin request policy forwards all viewer request headers to the origin *except* the `Host` header. This is critical because the origins are on different domains — they need to receive their own domain as `Host` (e.g., `web-docs.union.ai`), not `www.union.ai`, or they would reject the request.

**No caching**: CloudFront is not acting as a CDN cache here. Every request is proxied through to the origin. The Cloudflare Pages and Webflow backends handle their own caching and edge delivery.

### Phase 4: Backend Serving

After CloudFront routes the request, it reaches one of three backends (production origins shown):

- **web-docs.union.ai** (Cloudflare Pages) — Serves the v2 documentation. Hugo-generated static site built by GitHub Actions (`build-and-deploy.yml` on `unionai-docs` `main`) and pushed to CF Pages via Direct Upload. Staging equivalent: `staging.docs-dog.pages.dev`.
- **v1.docs-dog.pages.dev** (Cloudflare Pages) — Serves the v1 documentation. Same workflow on the `v1` branch, deployed with `--branch=v1`. Branch alias within the `docs` Pages project, shared between production and staging.
- **web.union.ai** (Webflow) — Serves the Union.ai corporate website (marketing pages, blog, pricing, etc.). Staging equivalent: `union-staging.webflow.io`.

### Phase 5: docs.flyte.org / docs-legacy.flyte.org Redirect Chain (Cloudflare — flyte.org zone)

Both `docs.flyte.org` and `docs-legacy.flyte.org` are handled entirely within Cloudflare in the Admin@flyte.org account. No CloudFront is involved. They share a single set of dual-host rules and feed into the union.ai bulk-redirect list via the `_r_/flyte` prefix convention.

#### Redirect Rules (Dynamic Redirects, processed in order)

All rules below filter on `http.host in {"docs.flyte.org" "docs-legacy.flyte.org"}` unless noted otherwise. Each is 302 with query-string preservation.

| # | Match (path) | Action | Purpose |
|---|--------------|--------|---------|
| 1 | `slack.flyte.org/` (different host) | 302 → Slack invite link | Community Slack shortcut |
| 2 | `/en/v*/*` | rewrite to `/en/latest/${2}` (same host) | Collapse main-project version → latest |
| 3 | `/projects/*/en/v*/*` | rewrite to `/projects/${1}/en/latest/${3}` (same host) | Collapse subproject version → latest |
| 4 | `/en/stable/*` | rewrite to `/en/latest/${1}` (same host) | Collapse main-project `stable` → latest |
| 5 | `/projects/*/en/stable/*` | rewrite to `/projects/${1}/en/latest/${2}` (same host) | Collapse subproject `stable` → latest |
| 6 | `*/index.html` | strip `/index.html` (same host) | Normalize section-index URLs |
| 7 | `*.html` | strip `.html` (same host) | Normalize extension |
| 8 | (catch-all on host) | rewrite to `https://www.union.ai/_r_/flyte{path}` | Send normalized path to bulk redirects |

Rules 2–7 normalize the URL (collapse versions, strip extensions) so the catch-all forwards a clean canonical path to the bulk-redirect lookup. Each rule uses Cloudflare's `wildcard_replace` (the `regex_replace` function is a Pro+ feature; the flyte.org zone is on Free).

> **Why `stable` needs its own pair of rules (rules 4–5, added 2026-08-11, DOC-1410).** ReadTheDocs served the same content under both `/en/latest/` and `/en/stable/`, and Google indexed both. Rules 2–3 only match `v*`, so `stable` was never collapsed — and the bulk-redirect list has ~1,465 entries for `en/latest` and **zero** for `en/stable`. Unmatched requests fell through to the catch-all, which blindly prepends the v2 prefix and leaves the RTD version token embedded in the path:
>
> ```
> docs.flyte.org/en/stable/deployment/gcp/manual.html
>   → www.union.ai/_r_/flyte/en/stable/deployment/gcp/manual/
>   → www.union.ai/docs/v2/flyte/stable/deployment/gcp/manual/   404
>                               ^^^^^^ never normalized away
> ```
>
> Adding the two `stable` twins fixed **78 of the 112** live 404s on these hosts, with **no regression** among the 202 that already worked (measured before and after). Two rules were preferred over duplicating ~1,465 CSV rows: the rule covers the entire `en/stable` family, including URLs beyond Search Console's 1,000-row export cap.
>
> **Generalise this before adding another alias:** any RTD alias that is not literally `v*` or `latest` has the same gap. If a `/en/<alias>/` form ever appears in the 404 report, it needs its own twin here — the catch-all will otherwise emit a plausible-looking but permanently broken URL rather than failing loudly.

**The catch-all mechanism (rule 8)**: After the normalization rules have had a chance to fire, any remaining request to either hostname is rewritten to `https://www.union.ai/_r_/flyte{path}`. The `_r_/flyte` prefix is a routing convention — these URLs match entries in the Union account's bulk redirect list, which maps them to specific `https://www.union.ai/docs/v2/flyte/<v2-path>` destinations.

**Why dual-host as a single rule set**: `docs.flyte.org` is the legacy public-facing Flyte docs hostname (Google-indexed since 2020). `docs-legacy.flyte.org` is the same RTD project's secondary custom domain (added later, also Google-indexed). Both served the same Sphinx content. After the v2 migration, both are now intercepted by the same Cloudflare rule chain and redirected to v2. A single shared rule set keeps the configuration in sync — flipping one hostname's behavior automatically flips the other.

**Historical**: prior to the consolidation, the flyte.org zone had ten rules including five specific URL-pattern rules (`*/api/flytekit/*`, `projects/flytekit/*`, `projects/cookbook/*`, `*/flytectl/*`, `en/*/_tags/*`) that collapsed many specific Flyte URLs into a handful of coarse v1 destinations. Those five rules were deleted as part of the v2 migration; the bulk-redirect list now provides per-page v2 mappings for those same URLs, preserving SEO precision.

#### Legacy Page Rules (flyte.org)

| Match | Target | Code |
|-------|--------|------|
| `blog.flyte.org/*` | `https://flyte.org/blog/*` | 301 |
| `slack.flyte.org/` | Slack invite link | 302 |
| `status.flyte.org/` | GitHub functional tests matrix | 302 |

The `slack.flyte.org` page rule is a duplicate of redirect rule #1 (likely a legacy leftover from before the redirect rules were created).

#### No Bulk Redirects in flyte.org account

The Admin@flyte.org account has no bulk redirect lists. All flyte.org path mapping is delegated to the Union account's bulk redirects via the `_r_/flyte` prefix mechanism.

## Request Flow Diagrams

Because the DNS records for `union.ai` and `www.union.ai` are **proxied** (orange cloud in Cloudflare), every request first hits the **Cloudflare edge**, which runs all zone rules (redirect rules, transform rules, bulk redirects). Only requests that are not redirected by Cloudflare rules are then forwarded to the CNAME target (CloudFront). This means Cloudflare always processes the request before CloudFront sees it.

### Request: `https://www.union.ai/docs/v2/byoc/getting-started`

```
Browser
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match (already has www, has version prefix)
    → Bulk redirects: no match
    → Pass through to CNAME target: d96tdta1ar9l3.cloudfront.net
  → CloudFront: /docs/v2/* matches /docs/* behavior (precedence 3) → web-docs.union.ai
  → Cloudflare Pages: serves v2 docs
```

### Request: `https://www.union.ai/docs/v1/flyte/user-guide`

```
Browser
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match (has version prefix)
    → Bulk redirects: no match
    → Pass through to CNAME target: d96tdta1ar9l3.cloudfront.net
  → CloudFront: /docs/v1/* matches behavior (precedence 1) → v1.docs-dog.pages.dev
  → Cloudflare Pages: serves v1 docs
```

### Request: `https://www.union.ai/docs/byoc/user-guide`

```
Browser
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rule 4: unversioned /docs/byoc/* → 302 to /docs/v1/byoc/*
  → Browser follows redirect to www.union.ai/docs/v1/byoc/user-guide
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match (now has version prefix)
    → Pass through to CloudFront
  → CloudFront: /docs/v1/* → v1.docs-dog.pages.dev
  → Cloudflare Pages: serves v1 docs
```

### Request: `https://www.union.ai/docs/`

```
Browser
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rule 3: /docs/ → 302 to /docs/v2/
  → Browser follows redirect to www.union.ai/docs/v2/
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match
    → Pass through to CloudFront
  → CloudFront: /docs/v2/* matches /docs/* behavior → web-docs.union.ai
  → Cloudflare Pages: serves v2 docs landing page
```

### Request: `https://union.ai/pricing`

```
Browser
  → Cloudflare edge (union.ai zone, proxied)
    → Redirect rule 1: bare domain → 302 to https://www.union.ai/pricing
  → Browser follows redirect to www.union.ai/pricing
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match
    → Pass through to CloudFront
  → CloudFront: /pricing matches default (*) behavior → web.union.ai
  → Webflow: serves pricing page
```

### Request: `https://docs.flyte.org/en/v1.13.0/user_guide/basics/launch_plans.html`

```
Browser
  → Cloudflare edge (flyte.org zone, proxied — no CloudFront involved)
    → Rule 5 (strip .html): → 302 to docs.flyte.org/en/v1.13.0/user_guide/basics/launch_plans/
  → Browser follows redirect
  → Cloudflare edge (flyte.org zone, proxied)
    → Rule 2 (collapse main version): → 302 to docs.flyte.org/en/latest/user_guide/basics/launch_plans/
  → Browser follows redirect
  → Cloudflare edge (flyte.org zone, proxied)
    → Rule 6 (catch-all): → 302 to www.union.ai/_r_/flyte/en/latest/user_guide/basics/launch_plans/
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Bulk redirect matches _r_/flyte/en/latest/user_guide/basics/launch_plans → 302 to www.union.ai/docs/v2/flyte/user-guide/core-concepts/runs-and-actions/
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match (has v2 prefix)
    → Pass through to CloudFront
  → CloudFront: /docs/v2/* → web-docs.union.ai
  → Cloudflare Pages: serves v2 Flyte docs
```

### Request: `https://docs-legacy.flyte.org/en/latest/user_guide/customizing_dependencies/imagespec.html`

Identical to the previous example with `docs-legacy.flyte.org` as the source hostname. The same dual-host rule chain fires:

```
Browser
  → Cloudflare edge (flyte.org zone, proxied)
    → Rule 5 (strip .html): → 302 to docs-legacy.flyte.org/en/latest/user_guide/customizing_dependencies/imagespec/
  → Browser follows redirect
  → Cloudflare edge (flyte.org zone, proxied)
    → Rule 6 (catch-all): → 302 to www.union.ai/_r_/flyte/en/latest/user_guide/customizing_dependencies/imagespec/
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Bulk redirect matches _r_/flyte/en/latest/user_guide/customizing_dependencies/imagespec → 302 to www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte/image/
  → Browser follows redirect → CloudFront → web-docs.union.ai → serves v2 page
```

### Request: `https://docs.union.ai/building-workflows/launch-plans`

```
Browser
  → Cloudflare edge (docs.union.ai zone, proxied)
    → Bulk redirect: docs.union.ai/building-workflows/launch-plans
      → 302 to https://www.union.ai/docs/byoc/user-guide/core-concepts/launch-plans
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rule 4: unversioned /docs/byoc/* → 302 to /docs/v1/byoc/*
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match (now has version prefix)
    → Pass through to CloudFront
  → CloudFront: /docs/v1/* → v1.docs-dog.pages.dev
  → Cloudflare Pages: serves v1 docs
```

## Docs versioning (v2.x.y.z) — in progress (DOC-1245)

The v2 docs are gaining **per-release semantic versioning** (`prds` `docs_versioning` PRD, DOC-1245). This section describes the target routing; it is **not fully live yet**. The design deliberately reuses the existing v1/v2 mechanism (separate builds served under `/docs/<version>/` path prefixes) rather than inventing a new one, and — critically — **requires no CloudFront change** (CloudFront is Terraform-managed; see Infrastructure Notes) and **no change to the ~8,142 existing redirects**.

### The URL model ("A2")

| URL | Content | Indexed? | Mechanism |
|-----|---------|----------|-----------|
| `/docs/v2/…` | **stable** = the newest cut | ✅ **yes** | the real served path — **canonical, SEO anchor** (unchanged as a URL; its *content* moves from "main" to "newest cut") |
| `/docs/stable/…` | = stable | — | **Cloudflare redirect rule → `/docs/v2/…`** (static — v2 is always the newest stable, so it never needs re-pointing) |
| `/docs/latest/…` | `main` (bleeding edge) | ❌ noindex | its own served path (rebuilt every merge) |
| `/docs/v2.5.11.0/…` | immutable pinned **older** cut | ❌ noindex | served per enumerated tag (older-than-stable only) |

**Only `/docs/v2` (stable) is indexed.** `/docs/latest` and every pinned `/docs/v2.x.y.z` are `noindex,nofollow`, so search stays concentrated on the one canonical surface. The newest cut is served **once**, at `/docs/v2` — it is **not** also published at `/docs/v2.<newest>.0` (no byte-identical duplicate tree, so nothing can cannibalize the canonical). A tag gets its permanent pinned `/docs/v2.x.y.z` URL when it is **superseded** (rotated out of `stable`). Pinned/latest URLs remain fully usable for direct links (support, bookmarks); they're just not search-indexed.

Making `/docs/v2` serve *stable* rather than *main* also fixes the original moving-target pain (§ Overview): the default/most-linked path now shows the **released** docs, not unreleased-feature docs.

### How it maps onto the existing layers

- **CloudFront: no change.** The existing `/docs/*` → `docs` origin behavior already catches `/docs/latest/…`, `/docs/stable/…`, and `/docs/v2.x.y.z/…` (none match the more-specific `/docs/v1*`). Nothing new to route.
- **The `docs` origin's deploy assembles the versions.** The production build (`build-and-deploy.yml`) additionally emits, into one dist served by `web-docs.union.ai`: `docs/latest/` (main, noindex), `docs/v2/` (the newest cut, indexed), and `docs/v2.x.y.z/` (each **older** enumerated tag, noindex — the newest is served only at `docs/v2`). Immutable pinned builds are cached (built once), so a normal merge rebuilds only `docs/latest/`; a cut rebuilds `docs/v2/` + rotates the outgoing stable into a new pinned tree. Orchestrated by **`unionai-docs-infra/scripts/build_versions.sh`** (line-aware: it derives the line from the stable tag, so the same script serves `/docs/v1` on the v1 branch and skips the `latest` build for a secondary line (v1, whose /docs/latest URL is v2's)), driven by **`versions.toml`** at the repo root.
- **One new Cloudflare redirect rule:** `/docs/stable/*` → `/docs/v2/*` (static). `/docs/latest` is served, not redirected.
- **No change to bulk redirects** — they keep landing on the real, canonical `/docs/v2/…` (no double-hop).

### Cutting a version — the one-merge model

**`versions.toml` (repo root) is the source of intent**: `stable` = the newest tag, served once at `/docs/v2`; `enumerated` = the **older** tags, each published as a pinned `/docs/v2.x.y.z` copy (the newest is never enumerated — no duplicate tree). A **cut is materialized at merge**: when a merge to `main` names a `stable` tag that doesn't exist yet, `build-and-deploy.yml` mints it (a `v2.x.y.z` git tag + per-variant manifest) as a **pre-build step**, then assembles — so the tag creation and the build happen in one job (no cut↔deploy race), and every path collapses to **a single human merge**.

Two symmetric "buttons" both open a `versions.toml`-bump PR (the version is always resolver-**computed**, never hand-typed):

- **`flyte-sdk` release (auto):** `regen-api-docs.yml` regenerates the API reference **and** folds the `versions.toml` promote (`x.y.z.0`) into the **same** regen PR. Merging it updates the API ref and advances `/docs/v2` in one action.
- **Manual cut:** a maintainer clicks Run workflow on `docs-cut.yml` → it opens a `versions.toml`-bump PR (`x.y.z.(z+1)`). Merging it is the cut.

Guards: the cut refuses to mint a tag off a `flyte-sdk` version not published on PyPI, and refuses if `versions.toml`'s `stable` disagrees with the resolver (a hand-edit / stale API-ref). Tooling: `tools/api_generator/manifest.py` (resolver, `--check`/`--promote`, version arithmetic), `scripts/cut-docs-version.sh` (materialize the tag), `scripts/build_versions.sh` (multi-version assembly), `docs-cut.yml` / `regen-api-docs.yml` / `build-and-deploy.yml` (workflows). `noindex` on latest + pinned builds is set by `run_hugo.sh` (`NOINDEX=true` → a site param the `seo-meta.html` partial reads). See the `docs_versioning` PRD §9.4.

## Infrastructure Notes

### Why CloudFront?

CloudFront's role is strictly path-based routing — stitching three separate backends under a single domain. It does not cache. This function could be replaced by Cloudflare Workers or Cloudflare redirect/transform rules, which would eliminate the Cloudflare → CloudFront → Cloudflare round trip for docs requests.

### Terraform

The CloudFront configuration is managed via Terraform. Manual changes to the CloudFront dashboard will be reverted on the next `terraform apply`. The Terraform configuration lives in a separate infrastructure repository.

### Bulk Redirect Limits

The bulk redirect list contains 8,142 entries (fetched via paginated API, 500 items per page). Cloudflare's limit for bulk redirect lists is 20,000 entries on the Enterprise plan, so there's substantial headroom.

### Redirect Status Codes

Almost all redirects use **302 (temporary)**. This means browsers and search engines do not cache the redirects permanently. If these mappings are considered stable, switching to 301 (permanent) would improve performance for repeat visitors and signal to search engines that the old URLs should be deindexed in favor of the new ones.
