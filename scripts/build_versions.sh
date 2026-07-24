#!/usr/bin/env bash
#
# Assemble the multi-version docs dist (DOC-1245, docs-versioning "A2" model).
# See ROUTING-ARCHITECTURE.md § "Docs versioning (v2.x.y.z)".
#
# Produces ONE dist serving every v2-line version under /docs/<version>/:
#   dist/docs/latest/  <- current checkout (main)  NOINDEX   bleeding edge
#   dist/docs/v2/      <- the `stable` tag          INDEXED   canonical stable
#   dist/docs/<tag>/   <- each `enumerated` tag     NOINDEX   immutable, cached
#
# Only /docs/v2 (stable) is indexed; latest + every pinned version are noindex,
# so search concentrates on the one canonical surface. /docs/stable and the
# /docs/latest alias are Cloudflare redirect rules, not built here.
#
# The plan comes from `versions.toml` at the repo root. Immutable /docs/<tag>/
# builds are skipped if the dir already exists (CI restores from cache), so a
# normal main-merge deploy rebuilds only /docs/latest; a cut rebuilds /docs/v2
# plus the one new pinned tag.
#
# Each version builds in its own git worktree so it gets a full, isolated
# pre-build (API docs, redirects, etc.) at that ref, then its /docs/<version>/
# subtree is copied into the combined dist.
#
# Usage: build_versions.sh [--dry-run]
#
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel)}"
VERSIONS_FILE="${REPO_ROOT}/versions.toml"
DIST="${REPO_ROOT}/dist"
VARIANTS="${VARIANTS:-flyte union}"

[ -f "$VERSIONS_FILE" ] || { echo "ERROR: $VERSIONS_FILE not found (see unionai-docs-infra/versions.toml.sample)" >&2; exit 1; }

# Read the plan (stable tag + enumerated pinned tags) from versions.toml.
_plan="$(mktemp)"
python3 - "$VERSIONS_FILE" > "$_plan" <<'PY'
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
print("STABLE=" + d.get("stable", ""))
print('ENUMERATED="' + " ".join(d.get("enumerated", [])) + '"')
PY
source "$_plan"
rm -f "$_plan"

[ -n "$STABLE" ] || { echo "ERROR: 'stable' not set in versions.toml" >&2; exit 1; }

# Build one version into dist/docs/<label>/ from a git ref, in an isolated worktree.
build_version() {  # $1=label(dist path)  $2=git-ref  $3=noindex(true/"")
  local label="$1" ref="$2" noindex="$3"
  if [ "$DRY_RUN" = 1 ]; then
    printf '  build  /docs/%-11s from %-11s noindex=%s\n' "$label" "$ref" "${noindex:-false}"
    return
  fi
  local wt; wt="$(mktemp -d)"
  git worktree add --detach "$wt" "$ref" >/dev/null
  ( cd "$wt"
    git submodule update --init --recursive >/dev/null 2>&1 || true
    REPO_ROOT="$wt" VERSION="$label" NOINDEX="$noindex" VARIANTS="$VARIANTS" \
      make -f unionai-docs-infra/Makefile dist )
  mkdir -p "$DIST/docs"
  rm -rf "$DIST/docs/$label"
  cp -R "$wt/dist/docs/$label" "$DIST/docs/$label"
  git worktree remove --force "$wt"
}

echo "==> docs version assembly (stable=$STABLE, enumerated=[$ENUMERATED])"

# latest = current checkout (main), noindex (bleeding edge)
build_version "latest" "HEAD" "true"

# v2 = the stable tag, INDEXED (the one canonical surface)
build_version "v2" "$STABLE" ""

# immutable pinned versions, noindex, cache-skipped
for tag in $ENUMERATED; do
  if [ "$DRY_RUN" != 1 ] && [ -d "$DIST/docs/$tag" ]; then
    echo "  skip   /docs/$tag (already built / cache hit)"
    continue
  fi
  build_version "$tag" "$tag" "true"
done

echo "==> assembled: $(cd "$DIST/docs" 2>/dev/null && ls -d */ 2>/dev/null | tr '\n' ' ' || true)"
