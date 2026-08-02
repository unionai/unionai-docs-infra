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
# latest defaults on; a secondary line (v1) sets `latest = false` (/docs/latest is v2's global URL).
print("BUILD_LATEST=" + ("0" if d.get("latest") is False else "1"))
PY
source "$_plan"
rm -f "$_plan"

[ -n "$STABLE" ] || { echo "ERROR: 'stable' not set in versions.toml" >&2; exit 1; }

# The "line" (v2 / v1) is just the stable tag's prefix — v2.5.12.0 -> v2,
# v1.16.23.0 -> v1. It's the dist path for the stable, indexed tree
# (/docs/<line>), which lets this one script serve both branches. Derived, so
# there's no extra field to keep in sync.
LINE="${STABLE%%.*}"

# Build one version into dist/docs/<label>/ from a git ref, in an isolated worktree.
build_version() {  # $1=label(dist path)  $2=git-ref  $3=noindex(true/"")  $4=landing(true/"")
  local label="$1" ref="$2" noindex="$3" landing="${4:-}"
  if [ "$DRY_RUN" = 1 ]; then
    printf '  build  /docs/%-11s from %-11s noindex=%-5s landing=%s\n' \
      "$label" "$ref" "${noindex:-false}" "${landing:-false}"
    return
  fi
  local wt; wt="$(mktemp -d)"
  git worktree add --detach "$wt" "$ref" >/dev/null
  ( cd "$wt"
    # Submodules don't reliably init inside a linked git worktree (a known git
    # worktree+submodule friction — it silently leaves the submodule dir empty,
    # so `make -f unionai-docs-infra/Makefile` then can't find the Makefile).
    # Try the normal init, then for any submodule that's still empty, populate it
    # from the superproject's already-checked-out copy (the checkout step's
    # `submodules: recursive` guarantees those are present). All current cut tags
    # pin the same infra, so the superproject copy is the right content; drop its
    # .git so the build treats it as plain files (it only reads them, and the
    # worktree's own git still resolves gitlink SHAs from the tree).
    git submodule update --init --recursive >/dev/null 2>&1 || true
    for sub in unionai-docs-infra unionai-examples; do
      if [ -z "$(ls -A "$sub" 2>/dev/null)" ] && [ -d "$REPO_ROOT/$sub" ]; then
        rm -rf "$sub"; cp -R "$REPO_ROOT/$sub" "$sub"; rm -rf "$sub/.git"
      fi
    done
    # The version selector (run_hugo's version_menu injection) + the footer's
    # manifest-gen both gate on versions.toml being present in the build root. The
    # worktrees are checked out from main/tags that don't carry it, so copy the
    # superproject's versions.toml in — it's the "versioning is on" signal (and this
    # whole script only runs when the superproject has it, so it stays inert otherwise).
    [ -f "$REPO_ROOT/versions.toml" ] && cp "$REPO_ROOT/versions.toml" versions.toml
    # /docs/latest is the live main tip, not a frozen cut: drop any committed
    # data/version-manifest.json so `base` regenerates it fresh (footer shows main's
    # current resolved versions). Pinned tags (/docs/<line>, /docs/<tag>) keep their
    # committed, backend-pinned manifest. (Inline-cut model, DOC-1245.)
    [ "$label" = "latest" ] && rm -f data/version-manifest.json
    # VERSION/VARIANTS as make command-line variables (not env): the inner
    # `make` build_dist.sh spawns picks up the top-level Makefile, whose
    # makefile.inc hardcodes `VERSION := v2` and would clobber an env VERSION.
    # Command-line vars override `:=` and propagate to sub-makes via MAKEFLAGS,
    # so each version actually builds into dist/docs/<label>/. NOINDEX stays env
    # (run_hugo reads it from the environment).
    REPO_ROOT="$wt" NOINDEX="$noindex" \
      make -f unionai-docs-infra/Makefile dist VERSION="$label" VARIANTS="$VARIANTS" )
  mkdir -p "$DIST/docs"
  rm -rf "$DIST/docs/$label"
  cp -R "$wt/dist/docs/$label" "$DIST/docs/$label"
  # The top-level landing pages (dist/index.html, dist/docs/index.html) point at
  # this version's default variant. Take them from the canonical build (v2/stable),
  # so a bare /docs or /docs/ lands on stable.
  if [ "$landing" = "true" ]; then
    [ -f "$wt/dist/index.html" ]      && cp "$wt/dist/index.html"      "$DIST/index.html"
    [ -f "$wt/dist/docs/index.html" ] && cp "$wt/dist/docs/index.html" "$DIST/docs/index.html"
  fi
  git worktree remove --force "$wt"
}

echo "==> docs version assembly (stable=$STABLE, enumerated=[$ENUMERATED])"

# latest = the branch tip (bleeding edge), noindex — but ONLY for a line that
# has one. LATEST_REF defaults to HEAD for local runs; CI sets it to the branch
# (origin/main for v2, origin/v1 for v1) so a tag-triggered cut still builds
# latest from the branch (github.ref is the tag on a tag trigger). A secondary line
# (v1) sets `latest = false`: its /docs/latest would be unreachable (the edge
# routes /docs/latest to the v2 deployment) and waste files against its budget.
if [ "$BUILD_LATEST" = 1 ]; then
  build_version "latest" "${LATEST_REF:-HEAD}" "true"
else
  echo "  skip   /docs/latest (latest = false for this line)"
fi

# <line> = the stable tag, INDEXED (the one canonical surface); also emits the
# top-level /docs landing pages (so a bare /docs lands on stable).
build_version "$LINE" "$STABLE" "" "true"

# ---------------------------------------------------------------------------
# Per-line runtime manifest (DOC-1330, defect 3).
#
# The version selector is rendered SERVER-SIDE and baked into every page. So a
# page only ever knows the OTHER line's entries as of ITS OWN last build: cut v2
# and every already-deployed v1 page still shows v2's previous stable number in
# the dropdown, and omits the pin the cut just created (that pinned tree exists
# and is served, but nothing on a v1 page links to it). Vice versa for v1.
#
# Fixing that by rebuilding the sibling line on every cut would couple the two
# deploys. Instead each line PUBLISHES ITS OWN state at a URL it owns, and the
# selector fetches its siblings at runtime (see header.html). A v1 cut updates
# /docs/v1/versions.json the moment v1 deploys; v2's pages pick it up on the next
# page load, with no v2 rebuild. Deliberately NOT one shared /docs/versions.json:
# that file would have to be written by a single deployment, which would then need
# rebuilding whenever the other line cut -- the same coupling, relocated.
#
# This describes THIS line only. Shape matches one `version_menu` group so the
# client re-renders without duplicating any badge logic -- but the rules are also
# implemented in run_hugo.sh for the baked render; keep the two in step.
if [ "$DRY_RUN" != 1 ] && [ -d "$DIST/docs/$LINE" ]; then
  python3 - "$DIST/docs/$LINE/versions.json" "$LINE" "$STABLE" "$BUILD_LATEST" "$ENUMERATED" <<'PY'
import json, sys
path, line, stable, latest, enum = sys.argv[1:6]
has_latest = latest == "1"

def vkey(t):
    try:
        return tuple(int(x) for x in t.lstrip("v").split("."))
    except Exception:
        return ()

items = []
if has_latest:                      # global /docs/latest, owned by the primary line
    items.append({"seg": "latest", "num": "", "badge": "LATEST"})
if stable:                          # /docs/<line> -- the moving stable pointer
    # Unconditional STABLE, on both lines -- the badge is NOT tied to has_latest
    # (DOC-1330). Must match run_hugo.sh; check-version-menu-parity.sh enforces it.
    items.append({"seg": line, "num": stable.lstrip("v"), "badge": "STABLE"})
for p in sorted({e for e in enum.split() if e}, key=vkey, reverse=True):
    items.append({"seg": p, "num": p.lstrip("v"), "badge": ""})

json.dump({"line": line, "items": items}, open(path, "w"))
PY
  echo "  wrote  /docs/$LINE/versions.json ($(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["items"]))' "$DIST/docs/$LINE/versions.json") entries)"
fi

# immutable pinned versions, noindex, cache-skipped
for tag in $ENUMERATED; do
  if [ "$DRY_RUN" != 1 ] && [ -d "$DIST/docs/$tag" ]; then
    echo "  skip   /docs/$tag (already built / cache hit)"
    continue
  fi
  build_version "$tag" "$tag" "true"
done

# Cache policy for the runtime manifests (DOC-1330, defect 3).
#
# The whole point of /docs/<line>/versions.json is to be FRESHER than the baked
# page, so a long edge TTL would defeat it -- a v1 cut would not be visible from a
# v2 page until the cached copy expired, which is the bug it exists to fix.
#
# `cache: no-cache` on the fetch only governs the BROWSER cache; it cannot reach an
# edge. So state the policy at the origin, where both layers can see it: Pages
# honours dist/_headers, and the deploy is `wrangler pages deploy ./dist`.
#
# CAVEAT, and it is a real one: production sits behind CloudFront IN FRONT OF
# Cloudflare. This header is what the origin says; whether CloudFront honours it
# depends on that distribution's cache policy, which is not visible from here. If a
# stale manifest is ever observed in production, check CloudFront before suspecting
# this file. 60s is short enough to be timely and long enough to absorb a crawl.
#
# Appended, not overwritten, so this never clobbers an existing _headers.
if [ "$DRY_RUN" != 1 ]; then
  { echo "# Runtime version manifests (DOC-1330) -- must stay fresher than the baked page."
    echo "/docs/*/versions.json"
    echo "  Cache-Control: public, max-age=60, must-revalidate"
  } >> "$DIST/_headers"
  echo "  wrote  _headers (versions.json max-age=60)"
fi

echo "==> assembled: $(cd "$DIST/docs" 2>/dev/null && ls -d */ 2>/dev/null | tr '\n' ' ' || true)"
