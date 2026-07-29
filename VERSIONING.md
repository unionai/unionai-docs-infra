# Docs versioning

How the Union/Flyte docs versioning system works (DOC-1245). This is the reference for
maintainers. For the step-by-step of cutting a version see
[`CUTTING-A-DOCS-VERSION.md`](./CUTTING-A-DOCS-VERSION.md); for the URL/edge routing see
[`ROUTING-ARCHITECTURE.md`](./ROUTING-ARCHITECTURE.md); for adding a new major line see
[`RUNBOOK-new-major-line.md`](./RUNBOOK-new-major-line.md).

## TL;DR

- Docs are published per **line** (a major SDK series): **v2** (current) and **v1**. Each
  line has a canonical stable URL (`/docs/v2`, `/docs/v1`) plus immutable pinned older
  versions (`/docs/v2.x.y.z`).
- The **primary** line (v2 today) additionally serves `/docs/latest` = the bleeding-edge
  `main` tip.
- Version bumps happen **automatically**: when any version-source repo releases, it
  signals this repo, which regenerates the API reference and opens a **draft PR** that,
  on merge, cuts a new docs version. You can also trigger a bump by hand.
- A cut is **one merge**: merging the version-bump PR materializes the tag (inline, on the
  branch) and rebuilds. Nothing auto-merges — a human always reviews.

## Lines and branches

| Line | Branch | Role | SDK (`api-packages.toml [docs_version]`) | `/docs/latest`? |
|---|---|---|---|---|
| **v2** | `main` | primary | flyte 2.x (`flyte-sdk`) | yes |
| **v1** | `v1` | secondary | flytekit 1.x (`flytekit`) | no |
| (future) **v3** | `main` | primary | flyte 3.x | yes |

- The **primary** line lives on `main` and owns the global `/docs/latest` URL (there is
  only one `/docs/latest`; the edge routes it to `main`'s deployment).
- A **secondary** line lives on its own branch and serves only its stable + pins (no
  `/docs/latest`). It still advances (new SDK releases cut new versions) — "secondary"
  is about the URL layout, not staleness.
- Each line is its own Cloudflare Pages deployment, so each has an independent
  20k-files-per-deployment budget (lines never compete on that ceiling).

## The version scheme: SDK-triple + z

A docs version is `vN.x.y.z`:

- `N.x.y` is the **SDK release** the docs are built against. The SDK (`flyte-sdk` for v2,
  `flytekit` for v1) drives this triple.
- `z` is a **docs patch counter**. When a *secondary component* releases but the SDK
  triple is unchanged, we cut `…​.z+1` instead of a new triple.

| Change | Bump | Example |
|---|---|---|
| SDK release (flyte-sdk / flytekit) | new triple, `z` resets to 0 | `v2.5.16.0` → `v2.5.17.0` |
| Secondary component release (plugins, backend, `union`) | `z` increments | `v2.5.16.0` → `v2.5.16.1` |

So the docs version reflects **any** component change, and the footer stamps every
component's exact version (see below).

## URL model ("A2")

| URL | Content | Indexed | Mechanism |
|---|---|---|---|
| `/docs/latest` | `main` tip (bleeding edge) | no (`noindex`) | its own served tree, rebuilt every merge |
| `/docs/v2` | stable = the newest cut, **canonical** | **yes** | the one indexed surface for the line |
| `/docs/v2.x.y.z` | pinned older versions | no | immutable, cached |
| `/docs/v1` | v1 stable | yes* | secondary line's canonical |
| `/docs/stable` | → `/docs/v2` | — | Cloudflare redirect rule |

\* whether a secondary line is indexed is a policy choice (see DOC-1291).

Key properties:

- **One indexed surface per line.** `latest` and every pin are `noindex,nofollow`, so
  search concentrates on `/docs/<line>` (the stable). No duplicate-content.
- **No duplicate trees.** The newest tag is served **once**, at `/docs/<line>` — never
  *also* at its numeric `/docs/vN.x.y.z`. A tag gets its permanent numeric URL only when
  it is superseded (rotated out of `stable`).
- **Routing.** Cloudflare/CloudFront sends `/docs/v1*` to the v1 deployment and `/docs/*`
  (everything else, incl. `/docs/latest` and `/docs/v2*`) to `main`'s deployment;
  `/docs/stable/*` → `/docs/v2/*` is a redirect rule. See `ROUTING-ARCHITECTURE.md`.

## Cut = tag = one merge (inline tags)

- **`versions.toml`** (at each branch's repo root) is the intent: `stable` = the newest
  tag, `enumerated` = the older pins.
- **Merging a `versions.toml` bump IS the cut.** `build-and-deploy` materializes the tag
  named by `stable` at the merge commit and rebuilds.
- The tag is **inline** on the branch (it *is* a commit on `main`/`v1`, findable by
  `git describe`/`git log`), and it pins a committed **`data/version-manifest.json`** (the
  footer's source, including the resolved backend version). Because the manifest is
  committed, the cut is a reproducible snapshot; because it's a normal tracked file on the
  branch, there is no detached "sidecar" commit.
- `build_versions.sh` regenerates the manifest for the `/docs/latest` build only, so
  latest's footer always reflects `main`'s live versions while pinned tags stay frozen.

## Automatic bumps (release signals)

Each version-source repo signals this repo on release (`repository_dispatch`), and CI
routes the signal to the right bump:

| Signal source (repo) | Docs bump | Line | Response |
|---|---|---|---|
| `flyte-sdk` (2.x) | **full** triple `n.x.y.0` | v2 | regen API ref + cut |
| `flytekit` (1.x) | **full** triple `n.x.y.0` | v1 | regen API ref + cut |
| `flyteplugins-union` | **z-bump** | v2 | update manifest + cut |
| `union` (the `union` SDK package) | **z-bump** | v1 | update manifest + cut |
| `flyteorg/flyte` (backend) | **z-bump** on the matching line (`v2.x`→v2, `v1.x`→v1) | v2/v1 | update manifest + cut |

- On an **SDK** signal, `regen-api-docs.yml` regenerates the API reference from the latest
  PyPI, then folds `versions.toml` (the new stable) **and** `data/version-manifest.json`
  into a **draft PR**.
- On a **secondary-component** signal, the docs API reference doesn't change, so CI opens
  a **z-bump** PR (bumps `versions.toml`'s `z` and re-stamps `data/version-manifest.json`
  with the new component version) without a full regen.
- A **daily schedule** also polls for drift, as a backstop if a signal is missed.
- **Nothing auto-merges.** Every path ends in a draft PR a human reviews and merges; the
  merge is the cut.

## Manual bumps (workflow dispatch)

Both bump kinds can be triggered by hand from CI — they are `workflow_dispatch` runs:

**Major / regen bump** (the SDK docs changed) — dispatch `regen-api-docs.yml`:

```bash
gh workflow run regen-api-docs.yml --ref main   # v2
gh workflow run regen-api-docs.yml --ref v1     # v1
```

or **Actions → "Regenerate API docs" → Run workflow →** pick the branch. It regenerates
from the latest PyPI, computes the next version (a full bump if the SDK triple advanced, a
z-bump if it didn't), and opens/updates the draft PR. Merge it to cut.

**Minor / z-bump** (a secondary component released; the SDK API is unchanged) — dispatch
`docs-cut.yml`:

```bash
gh workflow run docs-cut.yml --ref main   # v2 z-bump
gh workflow run docs-cut.yml --ref v1     # v1 z-bump
```

or **Actions → "Cut a docs version" → Run workflow →** pick the branch. It resolves the
next version (a z-bump when the SDK triple is already cut), writes `versions.toml` +
`data/version-manifest.json` (capturing the current component versions), and opens a draft
PR. Merge it to cut.

> **Branch note.** GitHub fires `schedule` and `repository_dispatch` **only from the
> default branch** (`main`). So the *automatic* paths and the daily poll run for the
> primary line; a **v1** bump must use `workflow_dispatch --ref v1` (manual), and v1
> *auto*-regen needs a main-side driver (see DOC-1293 / DOC-1296).

## The selector and the footer

- **`served-versions.toml`** (this repo) is the cross-line registry the version selector
  reads. It lists every line's `latest` / `stable` / `enumerated` pins, so the selector on
  any page can show all lines even though a given deployment only holds its own line.
  Keep it in sync when a line cuts (automation tracked in DOC-1292).
- **`layouts/partials/versions.html`** renders the selector as one flat list grouped by
  line, with `LATEST` / `STABLE` badges on the primary line and bare version numbers
  elsewhere. `scripts/run_hugo.sh` builds the menu JSON from `served-versions.toml`; the
  line order and which line owns `/docs/latest` are **derived** from the registry (the
  line with `latest = true`), so no code changes when lines are added.
- **`layouts/partials/version-footer.html`** stamps each page with the exact component
  versions from `data/version-manifest.json` (SDK, plugins, backend, the `union` SDK).

## Files at a glance

| File | Where | Role |
|---|---|---|
| `versions.toml` | each branch root | build plan: `stable` tag + older `enumerated` pins |
| `data/version-manifest.json` | committed by the cut, per tag | the footer's pinned component versions |
| `served-versions.toml` | this repo | cross-line registry the selector reads |
| `api-packages.toml [docs_version]` | each branch | which SDK/passenger/backend the line resolves |
| `scripts/build_versions.sh` | this repo | assembles the multi-version dist (line-aware) |
| `scripts/cut-docs-version.sh` | this repo | tags the cut (inline-first) |
| `tools/api_generator/manifest.py` | this repo | resolves the version + writes the manifest (`--check`/`--write`/`--promote`) |
| `.github/workflows/regen-api-docs.yml` | each branch | auto/manual regen + cut PR |
| `.github/workflows/docs-cut.yml` | each branch | manual version bump PR (no regen) |
| `.github/workflows/build-and-deploy.yml` | each branch | materializes the tag + deploys |

## Status (2026-07)

Live today: the v2 + v1 lines, inline tags, the `flyte-sdk` auto-signal, manual dispatch
(regen + cut), the flat selector, and the component footer.

Tracked follow-ups (Linear, all `docsy`-labelled):

- **DOC-1296** — release signals from `flytekit` / `flyteplugins-union` / `flyteorg/flyte`
  / `union`, so *every* component release auto-bumps (the "Automatic bumps" table above is
  the target; only `flyte-sdk` is wired today). Includes the main-side v1 driver.
- **DOC-1292** — auto-sync `served-versions.toml` on cut (done by hand today).
- **DOC-1295** — make `enumerated` (pins) a deliberate choice, not auto-rotate every cut.
- **DOC-1294** — link footer versions to PyPI / GitHub.
- **DOC-1291** — noindex the v1 line to concentrate search on v2.
- **DOC-1290** — moving redirect so the current stable's numeric URL resolves.
