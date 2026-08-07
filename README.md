# Union.ai Documentation Build System

This document describes how the Union.ai documentation platform works, including local development, production builds, the Cloudflare Pages deployment pipeline, LLM documentation generation, and CI checks.

## Repository structure

> **Role of this repo as a submodule:** `unionai-docs` (branches `main` and
> `v1`) pins this repo by commit. That pin is a **promotion gate** — infra
> merges reach production only when a docs branch bumps its pointer — and the
> single source that lets one build system serve both content lines. It is
> *not* a historical record: builds apply the branch tip's pin to every
> version tree, old pins included ("content is versioned; chrome is
> promoted" — VERSIONING.md, DOC-1329).

The docs system is split across three repositories:

- **[unionai-docs](https://github.com/unionai/unionai-docs)** — the parent repository containing version-specific content and configuration. Files that differ between `main` (v2) and `v1` branches live here: `content/`, `data/`, `linkmap/`, `include/`, `api-packages.toml`, `makefile.inc`, and CI workflows (`.github/`).
- **[unionai-docs-infra](https://github.com/unionai/unionai-docs-infra)** (this repo) — shared build infrastructure, imported as a git submodule at `unionai-docs-infra/` in the parent `unionai-docs` repo. This includes Hugo configuration (`hugo.toml`, `hugo.site.toml`, `hugo.ver.toml`, `config.*.toml`), layouts, themes, static assets (`static/`), Python tools (`tools/`), shell scripts (`scripts/`), the build `Makefile` and the API-generation makefiles (`Makefile.api.sdk`, `Makefile.api.plugins`), and redirect data. The contents are identical across both production branches.

A thin top-level `Makefile` in `unionai-docs` is a delegator: it reads the version-specific variables from `makefile.inc` and forwards all build targets to `unionai-docs-infra/Makefile` (the target list is enumerated explicitly there). It also provides submodule helpers that are *not* forwarded — `make init-infra` / `make update-infra` (this repo) and `make init-examples` / `make update-examples` (the examples repo).

A third repository, **[unionai-examples](https://github.com/unionai/unionai-examples)** (at `unionai-examples/`), contains example code and tutorial notebooks referenced by the documentation. It is imported as a git submodule at `unionai-examples/` in the parent `unionai-docs` repo.

## Table of contents

- [Requirements](#requirements)
- [Local development](#local-development)
  - [Developer experience](#developer-experience)
  - [Controlling the development environment](#controlling-the-development-environment)
  - [Changing variants](#changing-variants)
- [Managing tutorial pages](#managing-tutorial-pages)
- [Production builds](#production-builds)
  - [What `make dist` does](#what-make-dist-does)
  - [Testing the production build locally](#testing-the-production-build-locally)
- [Deployment (GitHub Actions + Cloudflare Pages)](#deployment-github-actions--cloudflare-pages)
  - [Production deploys](#production-deploys)
  - [Pull request previews](#pull-request-previews)
  - [Build provenance](#build-provenance)
- [API reference documentation](#api-reference-documentation)
- [Helm chart documentation](#helm-chart-documentation)
- [Redirect management](#redirect-management)
  - [How redirects work](#how-redirects-work)
  - [Automatic redirect detection](#automatic-redirect-detection)
  - [Deploying redirects to Cloudflare](#deploying-redirects-to-cloudflare)
- [LLM documentation pipeline](#llm-documentation-pipeline)
  - [Overview](#overview)
  - [Generated output structure](#generated-output-structure)
  - [Processing pipeline](#processing-pipeline)
  - [Section bundles](#section-bundles-sectionmd)
  - [Key implementation details](#key-implementation-details)
  - [Updating the LLM docs](#updating-the-llm-docs)
- [CI checks on pull requests](#ci-checks-on-pull-requests)
  - [Check API Docs](#check-api-docs-check-api-docs)
  - [Check Helm Docs](#check-helm-docs-check-helm-docs)
  - [Check Images](#check-images-check-images)
  - [Check Jupyter Notebooks](#check-jupyter-notebooks-check-jupyter)
  - [Check Redirects](#check-redirects-check-redirects)
  - [Check Links](#check-links-check-links)
  - [Check Generated Content](#check-generated-content-check-generated-content)
  - [Check LLM Bundle Notes](#check-llm-bundle-notes-check-llm-bundle-notes)
  - [Check Markdownlint](#check-markdownlint-check-markdownlint)
  - [Check Spelling](#check-spelling-check-spelling)
  - [Pull request build and preview](#pull-request-build-and-preview)
  - [Quick fix for most failures](#quick-fix-for-most-failures)

---

## Requirements

1. **Hugo (extended)** (>= 0.145.0; enforced by `scripts/pre-flight.sh`). CI builds with Hugo 0.161.1.

   ```
   brew install hugo
   ```

2. **Python** (>= 3.10; CI uses 3.12) for the build tools (API/Helm generators, LLM doc builder, shortcode processor, redirect and link tooling).

3. **[uv](https://docs.astral.sh/uv/)** — the Python build tools run under `uv run --project unionai-docs-infra`, which resolves dependencies from `unionai-docs-infra/pyproject.toml` on demand. If `uv` is not on `PATH`, `make dist` installs it automatically (the Cloudflare/CI runners don't ship it). Install it yourself for local development:

   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

4. **Local configuration file**

   Copy the sample configuration and customize it:

   ```
   cp hugo.local.toml~sample hugo.local.toml
   ```

   Review `hugo.local.toml` before starting development. See [Controlling the development environment](#controlling-the-development-environment) for available settings.

## Local development

Start the development server:

```
make dev
```

This launches the site at `localhost:1313` in development mode with hot reloading. Edit content files and the browser refreshes automatically.

### Developer experience

The development environment gives you live preview and variant-aware rendering. You can see content from all variants at once, highlight the active variant's content, and identify pages missing from a variant.

### Controlling the development environment

Change how the development environment works by setting values in `hugo.local.toml`:

| Setting              | Description                                                                                                      |
| -------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `variant`            | The current variant to display. Change this, save, and the browser refreshes automatically with the new variant. |
| `show_inactive`      | If `true`, shows all content that did not match the variant. Useful for seeing all variant sections at once.      |
| `highlight_active`   | If `true`, highlights the *current* content for the variant.                                                     |
| `highlight_keys`     | If `true`, highlights replacement keys and their values.                                                         |

### Changing variants

Variants are flavors of the site (flyte, union). During development, render any variant by setting it in `hugo.local.toml`:

```toml
variant = "union"
```

To show content from other variants alongside the active one:

```toml
show_inactive = true
```

To highlight the active variant's content (to distinguish it from common content):

```toml
highlight_active = true
```

### Missing content

Content may be hidden due to `{{< variant ... >}}` blocks. To see what's missing, adjust the variant show/hide settings in development mode.

For a production-like view:

```toml
show_inactive = false
highlight_active = false
```

For full developer visibility:

```toml
show_inactive = true
highlight_active = true
```

### Page visibility

The developer site shows in red any pages missing from the variant. For a page to exist in a variant, it must be listed in the `variants:` frontmatter at the top of the file. Clicking on a red page gives you the path you need to add.

See [Contributing docs and examples](https://union.ai/docs/flyte/community/contributing-docs) for authoring guidelines.

## Managing tutorial pages

Tutorials are maintained in the [unionai-examples](https://github.com/unionai/unionai-examples) repository and imported as a git submodule in the `unionai-examples` directory.

To initialize the submodule on a fresh clone:

```
make init-examples
```

To update the submodule to the latest `main` branch:

```
make update-examples
```

## Production builds

### What `make dist` does

```
make dist
```

This is the main production build command. `make dist` runs `scripts/build_dist.sh`, which orchestrates the whole pipeline (with per-step timing) in this order:

1. **Ensures `uv` is installed** (installs it if missing — the CI/Cloudflare runners don't ship it).
2. **`make base`** — pre-build and pre-flight checks, then converts Jupyter notebooks from `unionai-examples` to markdown and scaffolds the `dist/` tree.
3. **`make check-deleted-pages`** — warns about content files deleted without a redirect (non-fatal).
4. **API and Helm reference docs**, in one of three modes depending on the environment:
   - **CI / Cloudflare** (`CI` or `CF_PAGES` set): runs the `check-api-docs` and `check-helm-docs` *checks* (non-fatal) — regeneration is not done in the deploy build; drift is caught by the dedicated CI checks instead.
   - **Local with `FLYTE_SDK_PATH` set**: regenerates the API docs from your local SDK checkout and runs `make update-helm-docs`.
   - **Local (default)**: `make update-api-docs` + `make update-helm-docs` — regenerates both from the pinned package versions.
5. **`make update-redirects`** — detects moved pages and appends to `redirects.csv`. Runs *after* the API/Helm regen so the redirect detector sees the regenerated content dirs and doesn't flag them as removed pages.
6. **`make check-links`** — internal-link check (non-fatal).
7. **Hugo builds** — builds every variant in `$(VARIANTS)` (flyte, union) into `dist/`. Runs sequentially by default; set `PARALLEL_HUGO=true` to build variants in parallel. Each variant build also runs `process_shortcodes.py` to emit the per-page `page.md` files.
8. **`make llm-docs`** — generates the LLM-optimized bundles and indexes (`llms.txt`, `llms-full.txt`, `section.md`) for each variant.

`make dist` is the single command that regenerates everything. If CI checks are failing, running `make dist` locally and committing the changed files will usually fix them.

### Testing the production build locally

Serve the `dist/` directory with a local web server:

```
make serve PORT=4444
```

If no port is specified, defaults to `PORT=9000`. Open `http://localhost:<port>` to view the site as it would appear at its official URL.

## Deployment (GitHub Actions + Cloudflare Pages)

The docs are **built in GitHub Actions and uploaded to Cloudflare Pages via Direct Upload** (the `wrangler pages deploy` action). Cloudflare Pages' own build runner is **not** used — CF Pages is only the static host, and its automatic build-on-push is disabled for the `docs` project so GHA owns production end-to-end. (This replaced the earlier CF-native build; see DOC-1228.)

All build jobs use the same toolchain: `actions/checkout` with `submodules: recursive`, Hugo 0.161.1 (extended), Python 3.12, and `astral-sh/setup-uv`, then `make dist`.

### Production deploys

**Workflow: `.github/workflows/build-and-deploy.yml`** (in the parent `unionai-docs` repo).

Triggered on push to the branch's own production ref (`main` on the v2 line, `v1` on the v1 line) and on `workflow_dispatch`. It runs `make dist`, then deploys with:

```
wrangler pages deploy ./dist --project-name=docs --branch=<sanitized-branch> --commit-dirty=true
```

`--commit-dirty=true` is required because `make dist` regenerates tracked files (notebooks, API/Helm docs), so the tree is always dirty at deploy time. The deploy step has its own 10-minute timeout so a hung upload fails fast instead of eating the whole job budget (DOC-1229).

This workflow is **production-only**. It never runs on `pull_request`; previews are the two-stage pipeline below.

### Pull request previews

PR previews use a **two-stage, fork-safe pipeline** (DOC-1228), because GitHub does not expose repo secrets to `pull_request` runs from forks:

1. **`build-pr.yml`** (`on: pull_request`) — builds `make dist` with **no secrets** and uploads the `dist/` + PR metadata as an artifact. Its check-run is named `Build and deploy docs` (preserved from the old single-stage workflow) so the branch-protection required status check keeps matching.
2. **`deploy-pr-preview.yml`** (`on: workflow_run` after "Build PR" completes) — runs in the trusted base-repo context where secrets are available, downloads the prebuilt artifact, and deploys it to a per-branch CF Pages preview. It never checks out or executes untrusted PR code.

**This applies to `v1` PRs exactly as it does to `main` PRs.** Both branches carry the same `build-pr.yml` + `deploy-pr-preview.yml` pair, and a PR based on `v1` gets a preview of the v1 tree in the same `docs` Pages project.

#### Finding the preview URL

Stage 2 runs as a **detached `workflow_run`**, so it does **not** appear in the PR's checks list, and its run is filed under the repo's default branch rather than the PR branch. The only check you see on the PR is stage 1, `Build and deploy docs`, whose last step is `Upload PR dist artifact`. That is the expected end of that job. It is not evidence that the deploy is missing.

The preview surfaces instead as a **sticky PR comment** titled *GHA build & deploy preview*, posted by stage 2 roughly a minute after the build check goes green. It carries two links:

- **Branch alias** — `https://pr-<num>-<sanitized-branch>.docs-dog.pages.dev`, stable for the life of the PR and always pointing at the latest push.
- **This commit** — the immutable per-deployment URL for the exact commit.

Append the line's path to reach content: `/docs/v2/<variant>/` on a `main` PR, `/docs/v1/<variant>/` on a `v1` PR.

If the comment is not there yet, the deploy has not finished. Watch the **Deploy PR preview** workflow (filter Actions by that workflow name, not by your branch) rather than the PR's check list.

Preview deployments are preview-class in Cloudflare Pages, so every one carries an `x-robots-tag: noindex` response header. That is by design and keeps previews out of search (see DOC-1332 for the same mechanism on the v1 production alias).

#### Fallback: serve the CI artifact locally

When a preview is not available (the deploy failed, the secrets are unavailable, or you want to inspect the built tree offline), download the artifact stage 1 already produced and serve it:

```
gh run download <build-pr-run-id> --repo unionai/unionai-docs --dir out
cd out/pr-dist/dist && python3 -m http.server 8899
# then open http://localhost:8899/docs/v1/<variant>/   (or /docs/v2/<variant>/)
```

Find the run id from the `Build and deploy docs` check on the PR, or with `gh run list --branch <pr-branch> --workflow build-pr.yml`.

This exercises the same artifact CI built and deployed, which makes it a better check than a fresh local `hugo` build. What it cannot reproduce is anything the edge adds: redirects, headers, and CloudFront routing. For those, use the real preview.

### Build provenance

Each build writes `dist/docs/build-info.json` (served at `/docs/build-info.json`) recording the builder, workflow file, run URL, commit, and timestamp — so incident response can verify what's actually serving production.

## API reference documentation

The API reference under `content/api-reference/` is generated from Python package docstrings by `tools/api_generator` (driven by `Makefile.api.sdk` for the SDK/CLI docs and `Makefile.api.plugins` for plugin packages). The package set and pinned versions live in the parent repo's `api-packages.toml`.

```bash
make check-api-docs     # verify the committed docs match what the pinned packages generate
make update-api-docs    # regenerate content/api-reference/ + linkmap/flytesdk-linkmap.json
```

To regenerate from a local SDK checkout instead of the pinned release, set `FLYTE_SDK_PATH` and run `make dist` (or the `Makefile.api.sdk` target directly). Regeneration respects `__all__` and ignores `_`-prefixed and imported items.

## Helm chart documentation

Helm chart reference docs are generated by `tools/helm_generator` and regenerated as part of `make dist`.

```bash
make check-helm-docs      # verify committed Helm docs are current (CI gate)
make update-helm-docs     # regenerate the Helm reference content
make generate-helm-docs   # run the underlying generator directly
```

## Redirect management

### How redirects work

When content pages are moved or renamed, `redirects.csv` tracks the old-to-new URL mappings. These are deployed to Cloudflare as a Bulk Redirect List, so old URLs automatically redirect to the new locations.

Each row in `redirects.csv` has seven columns:

| Column | Description                |
| ------ | -------------------------- |
| 1      | Source URL                 |
| 2      | Target URL                 |
| 3      | HTTP status code (e.g., 302) |
| 4      | Include subdomains (TRUE/FALSE) |
| 5      | Subpath matching (TRUE/FALSE) |
| 6      | Preserve query string (TRUE/FALSE) |
| 7      | Preserve path suffix (TRUE/FALSE) |

### Automatic redirect detection

The `detect_moved_pages.py` script scans git history for file renames under `content/` and generates redirect entries for both variants. Run it with:

```
make update-redirects
```

This is also called automatically by `make dist`. To preview what it would add without writing the CSV:

```
make dry-run-redirects
```

A companion check, `make check-deleted-pages` (`check_deleted_pages.py`), verifies that every deleted content file has a corresponding redirect entry; `make dist` runs it as a non-fatal warning and CI enforces it (see [Check Redirects](#check-redirects-check-redirects)).

### Deploying redirects to Cloudflare

Redirects are deployed to Cloudflare automatically via GitHub Actions (`deploy-redirects.yml`) when `redirects.csv` is modified on the `main` branch. The `deploy_redirects.py` script reads the CSV, converts it to the Cloudflare API format, and replaces all items in the Bulk Redirect List with a single `PUT /accounts/{account_id}/rules/lists/{list_id}/items`, then polls the returned bulk-operation until it completes.

The workflow can also be triggered manually from the Actions tab in GitHub.

For local deployment (requires environment variables `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_LIST_ID`):

```
make deploy-redirects
```

For a dry run that parses the CSV without making API calls:

```
python3 tools/redirect_generator/deploy_redirects.py --dry-run
```

## LLM documentation pipeline

### Overview

The build generates LLM-optimized documentation at four levels of granularity, designed for AI coding agents and AI search engines:

| File | Scope | Description |
|------|-------|-------------|
| `page.md` | Per page | Clean Markdown version of every page, with links to other `page.md` files |
| `section.md` | Per section | Single-file bundle of all pages in a section (where enabled) |
| `llms.txt` | Per variant | Page index with H2/H3 headings, grouped by section |
| `llms-full.txt` | Per variant | Entire documentation as one file with hierarchical link references |

### Generated output structure

```
dist/docs/llms.txt                          # Root discovery: lists versions
dist/docs/v2/llms.txt                       # Version discovery: lists variants
dist/docs/v2/{variant}/
├── llms.txt                                # Page index with headings
├── llms-full.txt                           # Full consolidated doc
├── page.md                                 # Root page
├── user-guide/
│   ├── page.md                             # User Guide landing page
│   ├── task-configuration/
│   │   ├── page.md                         # Section landing page
│   │   ├── section.md                      # Section bundle (all pages concatenated)
│   │   ├── resources/
│   │   │   └── page.md
│   │   ├── caching/
│   │   │   └── page.md
│   │   └── ...
│   └── ...
└── ...
```

### Processing pipeline

The LLM docs are produced in two stages that run at different points in `make dist`:

**Stage 1: `process_shortcodes.py`** — Generates `page.md` files (runs during each variant's Hugo build, via the `variant` target)

1. Reads Hugo's Markdown output from `tmp-md/` (Hugo builds this alongside HTML via the MD output format).
2. Resolves all shortcodes: `{{< variant >}}`, `{{< code >}}`, `{{< tabs >}}`, `{{< note >}}`, `{{< key >}}`, `{{< llm-bundle-note >}}`, etc.
3. Writes the result as `page.md` alongside each `index.html` in `dist/`.
4. Converts all internal links to point to other `page.md` files using relative paths.

**Stage 2: `build_llm_docs.py`** — Generates bundles and indexes (runs via the `llm-docs` target, after all variants are built)

1. **Lookup tables**: Traverses all `page.md` files depth-first via `## Subpages` links, building a lookup table mapping file paths and anchors to hierarchical titles (e.g. `"user-guide/task-configuration/resources/page.md"` → `"Configure tasks > Resources"`).
2. **`llms-full.txt`**: Processes all pages, converting internal `page.md` links to hierarchical bold references (e.g. `**Configure tasks > Resources**`).
3. **Subpage enhancement**: Adds H2/H3 headings to `## Subpages` listings in `page.md` files.
4. **Section bundles**: Generates `section.md` for sections with `llm_readable_bundle: true`.
5. **Link absolutization**: Converts all relative links in `page.md` files to absolute URLs (`https://www.union.ai/docs/...`).
6. **`llms.txt`**: Creates the page index with headings and bundle references.

### Section bundles (`section.md`)

To enable a `section.md` bundle for a documentation section, two things are required in the section's `_index.md`:

1. Frontmatter: `llm_readable_bundle: true`
2. Body: `{{< llm-bundle-note >}}` shortcode (renders a note pointing to the bundle)

A CI check (`check-llm-bundle-notes`) verifies these are always in sync.

In section bundles, links to pages within the section become hierarchical bold references, while links to pages outside the section become absolute URLs.

### Key implementation details

**Link conversion in `llms-full.txt`:**
- Cross-page: `[Resources](../resources/page.md)` → `**Configure tasks > Resources**`
- Anchor: `[Caching](../caching/page.md#cache-versions)` → `**Configure tasks > Caching > Cache versions**`
- Same-page: `[Image building](#image-building)` → `**Container images > Image building**`
- External links preserved unchanged

**Hierarchy optimization:** Strips the `Documentation > {Variant}` prefix automatically.

**Error handling:** Missing files log warnings; broken links fall back to link text with context. A `link-issues.txt` report is written per variant.

### Updating the LLM docs

LLM documentation regenerates automatically as part of `make dist`. To regenerate only the LLM files:

```
make llm-docs
```

New pages are included automatically if linked via `## Subpages` in their parent's Hugo output. New variants are detected automatically.

## CI checks on pull requests

Pull requests run a set of GitHub Actions checks (defined in the parent `unionai-docs` repo under `.github/workflows/`), plus the two-stage PR build-and-preview pipeline. The content checks are listed below. A separate guard workflow, `block-v1-to-main.yml`, prevents merging `v1` content into `main`.

### Check API Docs (`check-api-docs`)

**What it checks:** Whether the committed API reference docs match what the latest SDK versions would generate.

**Why it fails:** The upstream `flyte-sdk` or plugin packages released a new version and the generated API docs in `content/api-reference/` are stale.

**How to fix:**
```bash
make update-api-docs
```
Then commit the changed files in `content/api-reference/` and `linkmap/flytesdk-linkmap.json`.

### Check Helm Docs (`check-helm-docs`)

**What it checks:** Whether the committed Helm chart reference docs match what the current charts would generate.

**Why it fails:** A Helm chart changed but the generated Helm docs weren't regenerated.

**How to fix:**
```bash
make update-helm-docs
```
Then commit the changed files.

### Check Images (`check-images`)

**What it checks:** That all images referenced in content files actually exist in the repository.

**Why it fails:** A content file references an image that doesn't exist, was deleted, or was moved without updating the reference.

**How to fix:** Ensure the image file exists at the path referenced in the markdown. Run `make check-images` locally to see which references are broken.

### Check Jupyter Notebooks (`check-jupyter`)

**What it checks:** That generated markdown from Jupyter notebooks is up to date with the source notebooks in `unionai-examples`.

**Why it fails:** A notebook in the examples submodule was updated but the generated markdown wasn't regenerated.

**How to fix:**
```bash
make update-examples    # pull latest notebooks
make dist               # regenerates everything including notebook markdown
```
Then commit the changed files.

### Check Redirects (`check-redirects`)

**What it checks:** That `redirects.csv` includes entries for all file renames detected in git history.

**Why it fails:** A content file was renamed or moved but the corresponding redirect entries weren't added to `redirects.csv`.

**How to fix:**
```bash
make update-redirects
```
Then commit the updated `redirects.csv`.

### Check Links (`check-links`)

**What it checks:** That all internal links in content files resolve to existing pages.

**Why it fails:** A link points to a page that doesn't exist, was moved, or has a typo in the path. Note that links to section pages must use the `/_index` suffix (e.g., `[Foo](./foo/_index)` not `[Foo](./foo)`).

**How to fix:** Run `make check-links` locally to see which links are broken. Fix the links in the source files. Patterns can be excluded via `.link-checker-exclude` in the repository root (regex patterns matched against `source_file:link_url`).

### Check Generated Content (`check-generated-content`)

**What it checks:** That generated content files (API docs, Jupyter notebook conversions, redirects) are up to date with their sources.

**Why it fails:** An upstream source changed (SDK release, notebook update, file rename) but the generated files weren't regenerated.

**How to fix:**
```bash
make dist
```
Then commit the changed files. This single command regenerates all generated content.

### Check LLM Bundle Notes (`check-llm-bundle-notes`)

**What it checks:** That `llm_readable_bundle: true` in frontmatter and the `{{< llm-bundle-note >}}` shortcode in the page body are always in sync for section `_index.md` files.

**Why it fails:** A section has `llm_readable_bundle: true` but is missing the shortcode, or vice versa.

**How to fix:** Either add the missing `{{< llm-bundle-note >}}` shortcode to the page body, or add `llm_readable_bundle: true` to the frontmatter. Both must be present together, or neither.

### Check Markdownlint (`check-markdownlint`)

**What it checks:** That changed Markdown content conforms to the repo's markdownlint rules.

**Why it fails:** A content file violates a lint rule (heading style, list formatting, etc.).

**How to fix:** Address the reported lint violations in the flagged files.

### Check Spelling (`check-spelling`)

**What it checks:** That content contains no unrecognized/misspelled words.

**Why it fails:** A new word isn't in the project dictionary, or is a genuine typo.

**How to fix:** Fix the typo, or add the intended term to the project's allowed-words list.

### Pull request build and preview

**What it does:** `build-pr.yml` builds the full site (`make dist`) for the PR and uploads it as an artifact; `deploy-pr-preview.yml` then deploys that artifact to a per-branch Cloudflare Pages preview (see [Pull request previews](#pull-request-previews)). The build half reports as the required `Build and deploy docs` status check.

**How to use:** Once the build check is green, wait for the sticky *GHA build & deploy preview* comment and open its **Branch alias** link. The deploy half is a detached `workflow_run` and never shows up in the PR's checks list, so the build check ending at `Upload PR dist artifact` is normal and does not mean the deploy was skipped. Works the same on `v1` PRs. If no preview appears, fall back to [serving the CI artifact locally](#fallback-serve-the-ci-artifact-locally).

### Quick fix for most failures

Running `make dist` locally regenerates everything: API docs, Helm docs, redirects, and notebook conversions. It's the single command that covers all the generated-file checks. Commit any changed files afterward.
