# Union.ai Web Routing Architecture

This document describes the complete request routing system that serves `union.ai`, `www.union.ai`, `docs.flyte.org`, and related domains. The system spans three services: Cloudflare (DNS + rules + static hosting), AWS CloudFront (path-based reverse proxy), and Webflow (corporate site).

## Overview

The public-facing domain is `union.ai` (and `www.union.ai`). All web traffic enters through Cloudflare DNS, passes through AWS CloudFront for path-based routing, and is ultimately served by one of three backends:

| Content | Backend | Platform |
|---------|---------|----------|
| v2 docs (`/docs/v2/`) | web-docs.union.ai | Cloudflare Pages |
| v1 docs (`/docs/v1/`) | v1.docs-dog.pages.dev | Cloudflare Pages |
| Corporate site (everything else) | web.union.ai | Webflow |

The legacy domain `docs.flyte.org` is handled entirely within Cloudflare (no CloudFront involvement) and redirects all traffic to `www.union.ai/docs/`.

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
| `signup.union.ai` | `cname.vercel-dns.com` | Yes | Signup → Vercel |
| `sandbox.union.ai` | `union.ai` | Yes | Redirects to signup (via page rule) |
| `flyte.org` | `cdn.webflow.com` | No | Flyte corporate site → Webflow |
| `slack.flyte.org` | `8.8.8.8` (dummy A record) | Yes | Proxied dummy; Cloudflare rules redirect to Slack invite |
| `blog.flyte.org` | `8.8.8.8` (dummy A record) | Yes | Proxied dummy; page rule redirects to flyte.org/blog |

**Important**: `docs.flyte.org` still has a CNAME pointing to `readthedocs.io`, but because the record is **proxied** (orange cloud), Cloudflare intercepts the request and applies its redirect rules before it ever reaches ReadTheDocs. The CNAME target is effectively unused.

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
| **docs-builder** | `docs-builder.pages.dev` | *(none)* | `main` |

The **docs** project serves both v2 docs (via `web-docs.union.ai`) and the legacy `docs.union.ai` domain. Each deployment also gets a unique preview URL (e.g., `621e958d.docs-dog.pages.dev`).

The **docs-builder** project is used for preview/staging deployments (e.g., `nelson-selfhosted.docs-builder.pages.dev`).

The v1 docs are served from `v1.docs-dog.pages.dev` — this is a deployment alias within the same **docs** Pages project, not a separate project.

The Admin@flyte.org account has **no Cloudflare Pages projects**.

### Workers Routes

Neither the `union.ai` zone nor the `flyte.org` zone has any Cloudflare Workers routes configured. All request processing is done via redirect rules, transform rules, page rules, and bulk redirects.

### Phase 2: Cloudflare Zone Rules (union.ai)

Before traffic reaches CloudFront, Cloudflare processes its own zone rules for `union.ai`. These rules run at the Cloudflare edge.

#### Redirect Rules (Dynamic Redirects)

Processed in order:

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

**Key behavior**: Any request to `www.union.ai/docs/{variant}/*` without an explicit version prefix (`v1` or `v2`) is redirected to v1. This is because v1 was the original URL structure before versioning was introduced. The bare `/docs/` path, however, redirects to v2 (the current default).

#### Transform Rules

- URLs containing the string `HhI8nGjMQ1x5SrxSip29` are rewritten to `/`. This appears to be a cleanup rule for a preview/staging URL token.

#### Legacy Page Rules

| Match | Target | Code |
|-------|--------|------|
| `sandbox.union.ai/*` | `https://signup.union.ai` | 302 |
| `docs.union.ai/HhI8nGjMQ1x5SrxSip29/*` | `https://docs.union.ai/*` | 302 |

#### Bulk Redirects (2,363 entries)

A single bulk redirect list named "redirects" containing 2,363 entries, broken down by source domain:

- **2,057 entries** from `www.union.ai` — These handle the migration from the original unversioned URL structure to the versioned structure. For example:
  - `www.union.ai/docs/byoc/user-guide/administration` → `https://www.union.ai/docs/v1/byoc/user-guide/administration`
  - `www.union.ai/docs/serverless/tutorials/...` → `https://www.union.ai/docs/v1/serverless/tutorials/...`
  - Cross-variant redirects (e.g., serverless page → byoc equivalent)

- **306 entries** from `docs.union.ai` — These handle the migration from the legacy `docs.union.ai` subdomain to `www.union.ai/docs/`. For example:
  - `docs.union.ai/` → `https://www.union.ai/docs`
  - `docs.union.ai/administration` → `https://www.union.ai/docs/byoc/user-guide/administration`
  - `docs.union.ai/building-workflows/launch-plans` → `https://www.union.ai/docs/byoc/user-guide/core-concepts/launch-plans`

All bulk redirects use 302 (temporary) status codes with query string preservation.

**Note**: These bulk redirects also serve as the landing zone for the `docs.flyte.org` catch-all (see Phase 5 below). The flyte.org catch-all rewrites `docs.flyte.org/*` to `www.union.ai/_r_/flyte/*`, which then matches entries in this bulk redirect list.

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
| 2 | `/_static/*` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 3 | `/docs/*` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 4 | `/docs` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 5 | `*` (default) | web | HTTPS only | Disabled | AllViewerExceptHostHeader |

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
| 2 | `/_static/*` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 3 | `/docs/*` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 4 | `/docs` | docs | HTTPS only | Disabled | AllViewerExceptHostHeader |
| 5 | `*` (default) | web | HTTPS only | Disabled | AllViewerExceptHostHeader |

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

- `/docs/v1` and `/docs/v1/*` (precedences 0-1) are checked before `/docs/*` (precedence 3), ensuring v1 docs requests go to the v1 origin, not the v2 docs origin.
- `/_static/*` (precedence 2) routes docs static assets to the v2 docs origin.
- `/docs` and `/docs/*` (precedences 3-4) catch all remaining docs traffic (v2 and anything else) and route to the v2 docs origin.
- Everything else hits the default behavior and goes to Webflow.

**AllViewerExceptHostHeader**: This origin request policy forwards all viewer request headers to the origin *except* the `Host` header. This is critical because the origins are on different domains — they need to receive their own domain as `Host` (e.g., `web-docs.union.ai`), not `www.union.ai`, or they would reject the request.

**No caching**: CloudFront is not acting as a CDN cache here. Every request is proxied through to the origin. The Cloudflare Pages and Webflow backends handle their own caching and edge delivery.

### Phase 4: Backend Serving

After CloudFront routes the request, it reaches one of three backends (production origins shown):

- **web-docs.union.ai** (Cloudflare Pages) — Serves the v2 documentation. This is a Hugo-generated static site deployed via Cloudflare Pages. Staging equivalent: `staging.docs-dog.pages.dev`.
- **v1.docs-dog.pages.dev** (Cloudflare Pages) — Serves the v1 documentation. Also a Hugo-generated static site on Cloudflare Pages. Shared between production and staging.
- **web.union.ai** (Webflow) — Serves the Union.ai corporate website (marketing pages, blog, pricing, etc.). Staging equivalent: `union-staging.webflow.io`.

### Phase 5: docs.flyte.org Redirect Chain (Cloudflare — flyte.org zone)

The legacy `docs.flyte.org` domain is handled entirely within Cloudflare in the Admin@flyte.org account. No CloudFront is involved.

#### Redirect Rules (Dynamic Redirects, processed in order)

| # | Match | Target | Code | Purpose |
|---|-------|--------|------|---------|
| 1 | `slack.flyte.org/` | Slack invite link | 302 | Community Slack shortcut |
| 2 | `docs.flyte.org/*/api/flytekit/*` | `union.ai/docs/flyte/api-reference/flytekit-sdk` | 302 | Flytekit API docs |
| 3 | `docs.flyte.org/projects/flytekit/*` | `union.ai/docs/flyte/api-reference/flytekit-sdk` | 302 | Alt flytekit path |
| 4 | `docs.flyte.org/projects/cookbook/*` | `union.ai/docs/flyte/user-guide` | 302 | Old cookbook → user guide |
| 5 | `docs.flyte.org/*/flytectl/*` | `union.ai/docs/flyte/api-reference/flytectl-cli` | 302 | flytectl docs |
| 6 | `docs.flyte.org/en/*/_tags/*` | `union.ai/docs/flyte/tags` | 302 | Tag pages |
| 7 | `docs.flyte.org/en/v*/*` | `docs.flyte.org/en/latest/*` | 302 | Strip version → latest |
| 8 | `docs.flyte.org/*/index.html` | Strip `index.html` | 302 | Clean URLs |
| 9 | `docs.flyte.org/*.html` | Strip `.html` extension | 302 | Clean URLs |
| 10 | `docs.flyte.org` (catch-all) | `www.union.ai/_r_/flyte{path}` | 302 | Send to bulk redirects |

**The catch-all mechanism (rule 10)**: After the specific redirect rules have had a chance to match, any remaining `docs.flyte.org` request is rewritten to `www.union.ai/_r_/flyte{path}`. The `_r_/flyte` prefix is a routing convention — these URLs match entries in the Union account's bulk redirect list, which maps them to the correct `www.union.ai/docs/v1/flyte/*` destination.

Rules 7-9 normalize the URL (strip version prefixes, `.html` extensions, `index.html`) before the catch-all fires, so that the bulk redirect lookup has a clean path to match against.

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

### Request: `https://docs.flyte.org/en/latest/user_guide/basics/tasks.html`

```
Browser
  → Cloudflare edge (docs.flyte.org, flyte.org zone, proxied — no CloudFront involved)
    → Redirect rule 9: strip .html → 302 to docs.flyte.org/en/latest/user_guide/basics/tasks
  → Browser follows redirect
  → Cloudflare edge (docs.flyte.org, flyte.org zone, proxied)
    → Redirect rule 10 (catch-all): → 302 to www.union.ai/_r_/flyte/en/latest/user_guide/basics/tasks
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Bulk redirect: www.union.ai/_r_/flyte/en/latest/... → 302 to www.union.ai/docs/v1/flyte/...
  → Browser follows redirect
  → Cloudflare edge (www.union.ai zone, proxied)
    → Redirect rules: no match (has version prefix)
    → Pass through to CloudFront
  → CloudFront: /docs/v1/* → v1.docs-dog.pages.dev
  → Cloudflare Pages: serves v1 Flyte docs
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

## Infrastructure Notes

### Why CloudFront?

CloudFront's role is strictly path-based routing — stitching three separate backends under a single domain. It does not cache. This function could be replaced by Cloudflare Workers or Cloudflare redirect/transform rules, which would eliminate the Cloudflare → CloudFront → Cloudflare round trip for docs requests.

### Terraform

The CloudFront configuration is managed via Terraform. Manual changes to the CloudFront dashboard will be reverted on the next `terraform apply`. The Terraform configuration lives in a separate infrastructure repository.

### Bulk Redirect Limits

The bulk redirect list contains 2,363 entries (fetched via paginated API, 500 items per page). Cloudflare's limit for bulk redirect lists is 20,000 entries on the Enterprise plan.

### Redirect Status Codes

Almost all redirects use **302 (temporary)**. This means browsers and search engines do not cache the redirects permanently. If these mappings are considered stable, switching to 301 (permanent) would improve performance for repeat visitors and signal to search engines that the old URLs should be deindexed in favor of the new ones.
