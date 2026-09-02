# Union.ai Web Routing Architecture

> **Last verified 2026-09-02** against the live Cloudflare rulesets and production responses.
> The Cloudflare rule tables below were read from the API, not from memory: dynamic redirects
> `b97ef0221e4b49a1a8c2cb614248b1f8` (17 rules, ruleset version 71) and transforms
> `60866e7f204a4848ba5ec3670ea095eb` (3 rules). Re-read both before trusting the tables, since
> they are edited in the dashboard and this file cannot know about it.
>
> Rule counts and status codes drift. If you are debugging, probe the live URL first
> (`curl -sI`), then reconcile with this document, and fix the document when it is wrong.

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
> **Verifying what is live: the documented command no longer works.** Each build still writes `dist/docs/build-info.json`, but the F3 fallback rule (Phase 2, rule 17) now intercepts any `/docs/*` path that is not a version root, so `https://www.union.ai/docs/build-info.json` returns a 302 to the user guide instead of the JSON. `/docs/v2/build-info.json` and `/docs/v1/build-info.json` are swallowed the same way by F1 and F2. The fallbacks carry explicit passthrough allowlists (`versions.json`, `llms.txt`, `sitemap.xml`); `build-info.json` was never added to them, so an incident-response diagnostic is unreachable at the edge. Tracked in DOC-1531. Until it is fixed, read the deployment's own URL instead, for example `curl https://<deployment-hash>.docs-dog.pages.dev/docs/build-info.json`, or read the GHA run directly.

The v1 docs are served from `v1.docs-dog.pages.dev` — this is a deployment alias within the same **docs** Pages project, not a separate project. Built and deployed by the same GHA workflow on the `v1` branch with `--branch=v1`.

The **docs-builder** project was previously used for preview/staging Git-connected builds. After the 2026-05 GHA-build migration it has no role; it remains in the dashboard as an orphan with auto-deploys disabled (1,714 historical deployments make bulk-delete via API expensive vs. its zero-cost inert state).

The Admin@flyte.org account has **no Cloudflare Pages projects**.

### Workers Routes

Neither the `union.ai` zone nor the `flyte.org` zone has any Cloudflare Workers routes configured. All request processing is done via redirect rules, transform rules, page rules, and bulk redirects.

### Phase 2: Cloudflare Zone Rules (union.ai)

Before traffic reaches CloudFront, Cloudflare processes its own zone rules for `union.ai`. These rules run at the Cloudflare edge.

#### Redirect Rules (Dynamic Redirects)

Ruleset `b97ef0221e4b49a1a8c2cb614248b1f8` (phase `http_request_dynamic_redirect`), processed in order. **17 rules, all enabled, ruleset version 71 as of 2026-09-02.** This is the only mechanism in the stack that can use regular expressions and capture groups; bulk redirect lists cannot (see "Which mechanism handles what" below).

| # | Match | Target | Code | Purpose |
|---|-------|--------|------|---------|
| 1 | `union.ai` (bare domain) | `https://www.union.ai{path}` | 302 | Canonicalize to www |
| 2 | `https://apps.*` | Strip `apps.` prefix | 301 | Legacy apps subdomain |
| 3 | `www.union.ai/docs/` (bare, with trailing slash) | `/docs/v2/` | 302 | Docs root defaults to v2 |
| 4 | `/docs/byoc/*` | `/docs/v1/byoc/*` | 302 | Unversioned byoc = v1 |
| 5 | `/docs/serverless/*` | `/docs/v1/serverless/*` | 302 | Unversioned serverless = v1 |
| 6 | `/docs/flyte/*` | `/docs/v1/flyte/*` | 302 | Unversioned flyte = v1 |
| 7 | `/docs/selfmanaged/*` | `/docs/v1/selfmanaged/*` | 302 | Unversioned selfmanaged = v1 |
| 8 | `staging.union.ai/try-2.0` | Staging Pages URL | 302 | Staging vanity URL |
| 9 | `/docs/stable/*` | `/docs/v2/*` | 302 | **`stable` alias.** Static: v2 always serves the newest cut, so this never needs re-pointing |
| 10 | `^/docs/[^/]+/[^/]+/_?section\.md$` | that tree's `llms.txt` | 301 | Retired bundle name at a variant root (DOC-1509) |
| 11 | `^/docs/[^/]+/[^/]+/.+/_?section\.md$` | `<path>.md` | 301 | Retired bundle name → the page twin (DOC-1509) |
| 12 | `^/docs/[^/]+/[^/]+/page\.md$` | that tree's `llms.txt` | 301 | Retired twin name at a variant root (DOC-1432) |
| 13 | `^/docs/[^/]+/[^/]+/.+/page\.md$` | `<path>.md` | 301 | Retired twin name → the page twin (DOC-1432) |
| 14 | exact list: `/docs.md`, `/docs/{latest,v2,v1}.md`, `/docs/{latest,v2,v1}/{union,flyte}.md` | that tree's `llms.txt` | 301 | A tree root has no twin of its own; its agent surface is the index (DOC-1432) |
| 15 | `/docs/v1` or `/docs/v1/*`, excluding the five variant prefixes, `versions.json` and `llms.txt` | `/docs/v1/union/user-guide/` | 302 | **F1: v1 fallback** (DOC-1330) |
| 16 | `/docs/v2` or `/docs/v2/*`, same exclusions | `/docs/v2/union/user-guide/` | 302 | **F2: v2 fallback** (DOC-1330) |
| 17 | `/docs` or `/docs/*`, excluding `^/docs/(v[0-9][^/]*\|latest\|stable)(/\|$)`, `/docs/llms.txt`, `/docs/sitemap.xml` | `/docs/v2/union/user-guide/` | 302 | **F3: docs-root fallback** (DOC-1358 / DOC-1320) |

**Key behaviors.**

- Any request to `/docs/{variant}/*` without a version prefix goes to **v1**, because v1 was the URL structure before versioning. The bare `/docs/` path goes to **v2**. Rules 4 to 7.
- Rules 10 to 13 exist because the agent-facing surface **consolidated on one shape, `<path>.md`**. The earlier names `page.md`, `section.md` and `_section.md` are retired, and these rules keep old links and old agent habits working with a single 301 rather than a 404. See the LLM-optimized documentation page for the shape that is current.
- **The three fallbacks are catch-alls, and they swallow more than 404s.** F1, F2 and F3 turn any unrecognized path under their prefix into a 302 to a user guide, so a missing file does not 404, it redirects. Each therefore needs an explicit passthrough allowlist for real non-page assets, and the allowlists are hand-maintained: F1 and F2 exempt `versions.json` and `llms.txt`, F3 exempts `/docs/llms.txt` and `/docs/sitemap.xml`. **Anything not on a list becomes unreachable the moment it is added to the build.** That is exactly how `/docs/build-info.json` was lost (see the Pages note in Phase 1, and DOC-1531). When you add a file at a tree root, add it to the allowlist in the same change.
- A path **inside** a variant tree still 404s properly, because the variant prefixes are excluded: `/docs/v2/union/nonexistent/` returns 404, verified 2026-09-02.

#### Transform Rules (URL rewrites, not redirects)

Ruleset `60866e7f204a4848ba5ec3670ea095eb` (phase `http_request_transform`), 3 rules. A rewrite changes the path Cloudflare fetches **without changing the URL in the browser**, which is why content negotiation lives here and not in the redirect ruleset.

| # | Match | Rewrite | Purpose |
|---|-------|---------|---------|
| 1 | path contains `HhI8nGjMQ1x5SrxSip29` | `/` | Cleanup for a preview/staging URL token |
| 2 | `/docs/*` **and** `Accept` contains `text/markdown`, at a variant root | that tree's `llms.txt` | **Content negotiation** (DOC-1501) |
| 3 | `/docs/*` **and** `Accept` contains `text/markdown`, any other page | the page's `.md` twin | **Content negotiation** (DOC-1501) |

So an agent can either append `.md` to a URL or send `Accept: text/markdown` to the ordinary URL and get the same Markdown back. Verified 2026-09-02: `curl -H "Accept: text/markdown" https://www.union.ai/docs/v2/union/user-guide/` returns `200 text/markdown; charset=utf-8`.

**Reading these rulesets requires the right token.** The dynamic redirect ruleset reads with `CF_TOKEN_UNION_RULES_EDIT`; the transform ruleset needs `CF_TOKEN_TRANSFORM_EDIT` and returns "request is not authorized" with the other. Both are in `~/.zshenv`.

#### Which mechanism handles what

Three mechanisms redirect on this zone, and the choice is not stylistic:

| Need | Mechanism | Why |
|---|---|---|
| A pattern with a capture group (`page.md` → `<path>.md`) | **Dynamic redirect rule** | Bulk redirect lists have no regex and no capture groups |
| One of thousands of fixed old-URL to new-URL pairs | **Bulk redirect list**, from `redirects.csv` in the repo | Version-controlled, reviewable, scales to thousands of rows |
| Serve different content at the same URL | **Transform rule** (rewrite) | A redirect would change the URL; content negotiation must not |

The repo shows what is version-controlled, not what is possible. Regex redirects **are** available on this zone and are in active use (rules 10 to 14); the absence of capture groups in `redirects.csv` is a property of bulk redirect lists, not of the plan.

#### Legacy Page Rules

| Match | Target | Code |
|-------|--------|------|
| `sandbox.union.ai/*` | `https://signup.union.ai` | 302 |
| `docs.union.ai/HhI8nGjMQ1x5SrxSip29/*` | `https://docs.union.ai/*` | 302 |

#### Bulk Redirects

A single bulk redirect list named "redirects", sourced from `unionai-docs-infra/redirects.csv`, which holds **8,756 rows as of 2026-09-02**. Broken down by source prefix:

- **~6,256 entries** from `www.union.ai/*` and `docs.union.ai/*` (legacy categories):
  - `www.union.ai/docs/byoc/*` style mappings (unversioned → versioned)
  - `docs.union.ai/*` mappings to `www.union.ai/docs/...`

- **~1,520 entries** under `www.union.ai/_r_/flyte/*` — the landing zone for the `docs.flyte.org` / `docs-legacy.flyte.org` catch-all (see Phase 5). Sources are of the form:
  - `www.union.ai/_r_/flyte/en/latest/<canonical-path>` (main flyte project)
  - `www.union.ai/_r_/flyte/projects/<subproject>/en/latest/<canonical-path>` (subprojects: flytectl, cookbook, flytekit, flyteidl)
  
  Each entry targets a specific `https://www.union.ai/docs/v2/flyte/<v2-path>` destination. Most entries use `subpath_matching=TRUE` to absorb trailing-slash and minor URL-form variants.

**The bulk list is deliberately mixed, not one code.** Counted 2026-09-02 across the 8,756 rows: **6,357 are 302 and 2,399 are 301**, and that split is a policy rather than drift. A 301 is cached by the browser and effectively permanent, so it is used where the target *is* the content the old URL described. A 302 is used where the target is a judgment call, typically a retired page landing on the nearest sensible ancestor, so a better target stays reachable later. **Do not normalise these to a single code.** The reasoning is in `README.md` › "Choosing 301 vs 302".

Query strings are preserved either way. The dynamic redirect rules in the table above are mostly 302; the retired-name rules (10 to 14) are 301, because those names are retired for good.

The list is deployed by `tools/redirect_generator/deploy_redirects.py` via the `deploy-redirects.yml` workflow, which runs on `workflow_dispatch` and on pushes to `main` or `v1` that touch the `unionai-docs-infra` pointer or `versions.toml`. **One Cloudflare list serves both branches**, so the workflow is serialized on a single concurrency group that is deliberately not keyed on the branch, and never cancels in progress: each run replaces the whole list, so two in flight are a last-writer-wins race, and back-to-back runs spend an account-wide bulk-operation budget (five runs in twenty minutes hit Cloudflare's rate limit twice on 2026-08-26).

**Note**: The `_r_/flyte/*` portion of this list is the landing zone for the `docs.flyte.org` / `docs-legacy.flyte.org` catch-all (see Phase 5). After the flyte.org-zone normalization rules rewrite a request to `www.union.ai/_r_/flyte/<canonical-path>`, this list maps it to the canonical v2 destination. Sourced from `unionai-docs-infra/redirects.csv` and deployed by `tools/redirect_generator/deploy_redirects.py` via the `unionai-docs/.github/workflows/deploy-redirects.yml` CI workflow (auto-runs when the infra submodule pointer or either line's `versions.toml` changes on `main` or `v1`). **Not every item comes from the CSV:** redirects for retired version pins are derived at deploy time from the `retired` list in each line's `versions.toml`, so a pin's retirement and its redirect cannot drift apart — see VERSIONING.md › "Retired pins redirect themselves".

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

## Docs versioning (v2.x.y.z) — LIVE since 2026-07-29 (DOC-1245)

Per-release semantic versioning is **live on both lines** (`prds` `docs_versioning` PRD, DOC-1245). As of 2026-09-02 the v2 line serves `v2.6.10.0` at `/docs/v2` and the v1 line serves `v1.16.28.3` at `/docs/v1`. The design reuses the existing v1/v2 mechanism (separate builds served under `/docs/<version>/` path prefixes), and it needed **no CloudFront change** (CloudFront is Terraform-managed; see Infrastructure Notes) and **no change to the existing bulk redirects**.

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
- **One Cloudflare redirect rule:** `/docs/stable/*` → `/docs/v2/*` (static). This is live as rule 9 in the Phase 2 table. `/docs/latest` is served, not redirected.
- **No change to bulk redirects** — they keep landing on the real, canonical `/docs/v2/…` (no double-hop).

### What is actually served today (verified 2026-09-02)

| URL | Serves | `robots` meta |
|---|---|---|
| `/docs/v2/…` | `v2.6.10.0`, the newest cut | `index,follow` |
| `/docs/latest/…` | `main`, rebuilt every merge | `noindex,nofollow` |
| `/docs/v2.6.9.0/…` and the other enumerated pins | that immutable cut | `noindex,nofollow` |
| `/docs/stable/…` | 302 to `/docs/v2/…` | n/a |
| `/docs/v1/…` | `v1.16.28.3` | `index,follow` |

Only the two stable trees are indexed. The `robots` values above were read off the live pages, not inferred.

### Pin retention has a hard ceiling

`enumerated` cannot grow indefinitely. **Cloudflare Pages caps one deployment at 20,000 files, and each enumerated pin materializes another full site tree of roughly 3,000 files.** Seven trees broke the deploy outright on 2026-08-21. The list is therefore kept short on purpose, currently three pins per line, and this is a deploy constraint rather than a cost or reader-experience judgement.

### Retired pins redirect themselves

A pin leaves `enumerated` and arrives in `retired` in the same edit, made by `manifest.py --promote`. **Its redirect is derived from the `retired` list at deploy time, so do not add a row to `redirects.csv` for it** (DOC-1497). The pin's retirement and its redirect cannot drift apart because they come from one source. Verified 2026-09-02: `/docs/v2.6.0.0/union/user-guide/` returns 301 to `/docs/v2/union/user-guide/`.

`versions.toml` also carries two per-line switches: `latest` (whether the line owns the global `/docs/latest` URL, true only for the primary line) and `indexed` (whether the line's stable tree is search-indexed, defaulting to true).

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

The list holds 8,756 rows as of 2026-09-02 (fetched via paginated API, 500 items per page). Cloudflare's limit for bulk redirect lists is 20,000 entries on the Enterprise plan, so there is substantial headroom. The tighter ceiling in this stack is the **20,000 files per Pages deployment** cap, which is what bounds pin retention (see "Pin retention has a hard ceiling").

### Redirect Status Codes

The bulk list is **mixed on purpose**: 6,357 rows at 302 and 2,399 at 301 as of 2026-09-02. An earlier version of this document recommended normalising everything to 301 for SEO. **Do not do that.** The choice is about reversibility, not ranking: a 301 is cached by the browser and cannot cleanly be taken back, so it is right only where the target is certain. See `README.md` › "Choosing 301 vs 302". The dynamic redirect rules are likewise a mix: canonicalization and fallbacks are 302, retired-name rules are 301.

### Redirect Hygiene

Two properties are maintained deliberately and are easy to break:

- **One hop.** A redirect should land on a final URL, not on another redirect. 442 rows were retargeted in a 2026-08-31 pass to flatten chains, with 60 of 60 spot-checks verified one-hop. When you add a row, point it at the page that actually serves, not at a URL you know will redirect again.
- **Trailing slashes.** An exact-match row misses the other form and produces a soft 404. Either set `subpath_matching=TRUE` or enumerate both `/x` and `/x/`. See `CLOUDFLARE-REDIRECT-FIX.md`.
