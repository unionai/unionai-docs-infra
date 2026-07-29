# Cutting a docs version — runbook

How a new docs version (`v2.x.y.z`) gets published, end to end: what happens outside
the docs repo, what a person actually does, and how it flows through the systems.

Companion to [`ROUTING-ARCHITECTURE.md`](./ROUTING-ARCHITECTURE.md) (the URL/routing model)
and the `docs_versioning` PRD §9.4 (the design). Scheme: **DOC-1245**.

> **Status:** the machinery is built and feature-gated on `versions.toml`. Until that
> file exists at the repo root, everything here is inert and the build is single-version
> (`make dist`). "Go-live" = adding `versions.toml` + the one Cloudflare `stable → v2` rule.

---

## The one rule

**`versions.toml` (repo root) is the source of intent. Merging a change to it *is* the cut.**

```toml
# versions.toml
stable = "v2.5.13.0"        # newest tag, served at /docs/v2 (the public default)
enumerated = [              # OLDER tags only, each at a pinned /docs/v2.x.y.z copy.
  "v2.5.12.0",              # the current stable is NEVER here (no duplicate tree) --
  "v2.5.11.0",              # `--promote` rotates the outgoing stable in for you.
]
```

When a merge to `main` names a `stable` tag that doesn't exist yet, `build-and-deploy`
**materializes it** (mints the `v2.x.y.z` git tag + manifest) as a pre-build step, then
builds `/docs/v2` from it — all in one job, so the cut and the build never race. The
version is always **computed by the resolver**, never hand-typed.

There are two ways a `versions.toml` bump gets proposed. Both end in a single human merge.

---

## The cast

**People**

| Role | Where they act |
|---|---|
| **SDK engineer** | ships `flyte` releases in `flyteorg/flyte-sdk` |
| **Plugin owner** | ships `flyteplugins-union` (separate repo, own 0.x cadence) |
| **Docs maintainer** | reviews + merges the PRs; decides when to manual-cut; owns `versions.toml` + the Cloudflare rule |

**Automated systems**

| System | Role |
|---|---|
| `flyte-sdk/publish.yml` + **PyPI** + **docsy-bot App** | release → PyPI → cross-repo signal |
| `regen-api-docs.yml` | regenerate the API reference; fold the `versions.toml` promote (SDK path) |
| `docs-cut.yml` | manual-cut dispatch → opens a `versions.toml`-bump PR |
| `build-and-deploy.yml` | materialize the named stable tag (cut) → assemble multi-version dist → deploy |
| `cut-docs-version.sh` / `manifest.py` / `build_versions.sh` | the tag/resolver/assembly tooling |
| **Cloudflare Pages** + **AWS CloudFront** | host + route (`/docs/*`); CloudFront is Terraform-managed |

---

## Scenario A — an SDK release (the common case: `flyte 2.5.13` ships)

The trigger is **external to docs**: someone releases the SDK. The docs maintainer does **one merge**.

| # | Who / what | Where | Action |
|---|---|---|---|
| 1 | **SDK engineer** | `flyteorg/flyte-sdk` | Publishes **GitHub Release `v2.5.13`**. `setuptools_scm` derives the version from the tag. |
| 2 | `publish.yml` | flyte-sdk → **PyPI** | Auto-builds + uploads `flyte 2.5.13` **and every in-repo plugin** (lockstep). |
| 3 | `publish.yml` → `notify-docs` | → `unionai-docs` | Fires `repository_dispatch(sdk-release, 2.5.13)` as the **docsy-bot App**. |
| 4 | `regen-api-docs.yml` | `unionai-docs` | Regenerates the API reference (version → 2.5.13) **and folds the `versions.toml` promote** (`stable = v2.5.13.0`) into **one draft PR**. Nothing auto-merges. |
| **5** | **Docs maintainer** | `unionai-docs` | **Reviews + merges the one PR.** ← *the single human action.* |
| 6 | `build-and-deploy.yml` | `unionai-docs` | On the merge push: `versions.toml` names uncut `v2.5.13.0` → **cut pre-step** mints the tag (guard: 2.5.13 is on PyPI ✓) → `build_versions.sh` assembles `/docs/latest` (main, noindex), `/docs/v2` = **2.5.13.0** (indexed), `/docs/v2.5.13.0/` (pinned, noindex) → deploy. |
| 7 | CloudFront + CF rule | edge | Already route `/docs/*`; `/docs/stable/*` → `/docs/v2/*`. **No change.** |

**Result:** `/docs/v2/` (and `/docs/stable/`) now show `2.5.13`; `2.5.12.0` stays frozen at
`/docs/v2.5.12.0/`. The SDK team did their normal release; the docs maintainer did **one merge**.

---

## Scenario B — a manual cut (SDK unchanged, but drift piled up)

The trigger is **a human decision in docs**. Two steps: click the dispatch, then merge the PR it opens.

Example: `flyteplugins-union 0.6.0` shipped, the backend rolled, and content edits merged —
all on `/docs/latest`, none of which cuts a version on its own.

| # | Who / what | Where | Action |
|---|---|---|---|
| 1 | Plugin owner / content | (external) + `unionai-docs` | `flyteplugins-union 0.6.0`, backend updates, content merges land on `/docs/latest`. **No cut.** |
| **2** | **Docs maintainer** | GitHub Actions UI | Clicks **Run workflow** on `docs-cut.yml`. (Optionally `dry_run` first to preview the version.) |
| 3 | `docs-cut.yml` | `unionai-docs` | Resolves the next version (SDK unchanged → `z+1` = `v2.5.13.1`), runs `manifest.py --promote`, **opens a draft `versions.toml`-bump PR**. |
| **4** | **Docs maintainer** | `unionai-docs` | **Merges the PR.** ← *the second step.* |
| 5 | `build-and-deploy.yml` | `unionai-docs` | Same as A#6: cut pre-step mints `v2.5.13.1`, `/docs/v2` = **2.5.13.1** (now includes the plugin + backend + content drift), `/docs/v2.5.13.1/` published. |

**Result:** an immutable snapshot of the accumulated non-SDK drift, promoted to the public default.

---

## Side by side

| | **A: SDK release** | **B: Manual cut** |
|---|---|---|
| **Trigger** | SDK GitHub Release (external) | Docs maintainer clicks *Run workflow* |
| **Version** | new `x.y.z.0` (`z` resets) | same triple, `z+1` |
| **API-ref regen?** | yes (new SDK) | no (snapshots current `main`) |
| **Who opens the `versions.toml` PR** | `regen-api-docs.yml` (folded into the regen PR) | `docs-cut.yml` |
| **Human steps** | **1** (merge the regen PR) | **2** (dispatch, then merge) |
| **Captures** | the new SDK release | accumulated non-SDK drift |

Everything else — PyPI upload, the cross-repo signal, tag creation, the multi-version build,
the deploy — is automatic.

---

## Guards (why a mistake can't publish a bad version)

- **Non-PyPI SDK → refused.** A cut refuses to tag off a `flyte-sdk` version that isn't a
  published PyPI release (catches a hand-edited API-ref frontmatter, or a regen from a
  local/dev SDK build with a `setuptools_scm` dirty-tree version).
- **`versions.toml` vs. resolver mismatch → refused.** If `stable` names a tag that doesn't
  equal what the resolver computes (a hand-edit or a stale API-ref), the cut refuses rather
  than mint an unintended tag.
- **Version is computed, not typed.** `manifest.py --promote` writes the resolver's value;
  no human types a version string.
- **Immutable + monotonic.** Pinned `/docs/v2.x.y.z` builds are `noindex` and cached (built
  once); `z` only increments. A bad `versions.toml` fails the build loudly rather than
  serving something wrong.

A PyPI outage during a cut is a **warning, not a block** (the guard can't verify, so it proceeds).

---

## Go-live checklist (one-time, when versioning switches on)

1. Add `versions.toml` at the `unionai-docs` repo root (via `manifest.py --promote`, or the
   first `docs-cut.yml` dispatch) — this flips `build-and-deploy` from `make dist` to the
   multi-version assembly.
2. Add the Cloudflare redirect rule `/docs/stable/*` → `/docs/v2/*` (static; see
   `ROUTING-ARCHITECTURE.md`).
3. Confirm on the Cloudflare preview that `/docs/latest`, `/docs/v2`, and a pinned
   `/docs/v2.x.y.z/` all serve, and that only `/docs/v2` is `index,follow`.

Until step 1, the whole system is inert — merging the tooling PRs is a no-op on the live site.
