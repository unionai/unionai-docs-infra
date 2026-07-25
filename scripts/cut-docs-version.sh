#!/usr/bin/env bash
#
# Cut a docs version (DOC-1245, prds docs_versioning §9.4).
#
# Resolves the manifest, writes the combined data/version-manifest.json, commits
# it on a detached HEAD (so `main` never advances), tags the commit v2.x.y.z, and
# optionally pushes the tag. The tag pins the manifest, giving an immutable,
# reproducible snapshot; `main` (and /docs/latest) is left untouched.
#
# Two triggers call this (§9.4):
#   * a flyte-sdk release  -> --auto  (proceeds only if this is an sdk-release cut)
#   * a maintainer manual cut -> default (always proceeds; increments z)
#
# Usage: cut-docs-version.sh [--auto] [--push] [--dry-run]
#   --auto     no-op unless the next cut is an sdk-release cut (z==0). The on-push
#              trigger uses this so a plain content merge never cuts.
#   --push     push the tag to origin (default: local tag only).
#   --dry-run  print the decision and stop; no manifest, commit, or tag.
#
set -euo pipefail

AUTO=0 PUSH=0 DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --auto) AUTO=1 ;;
    --push) PUSH=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel)}"
TOOL="$REPO_ROOT/unionai-docs-infra/tools/api_generator/manifest.py"
MANIFEST_REL="data/version-manifest.json"

cd "$REPO_ROOT"

# Resolve the version decision (machine-readable). Sets DOCS_TAG/DOCS_DOCS_VERSION/
# DOCS_CUT_KIND/DOCS_Z.
eval "$(REPO_ROOT="$REPO_ROOT" uv run --quiet "$TOOL" --check --format shell)"

if [ -z "${DOCS_TAG:-}" ]; then
  echo "cut: could not resolve a version (flyte-sdk unknown) -- aborting" >&2
  exit 1
fi

echo "cut: next version ${DOCS_TAG} (${DOCS_CUT_KIND}, z=${DOCS_Z})"

# Safety guard (DOC-1245): the docs version is pinned to flyte-sdk, so refuse to
# cut off an SDK version that isn't a published release on PyPI. Catches a
# hand-edited API-ref frontmatter or a regen from a local/dev SDK build
# (setuptools_scm dirty-tree version). A PyPI outage (unknown) warns, not blocks.
case "${DOCS_SDK_ON_PYPI:-unknown}" in
  false)
    echo "cut: flyte-sdk ${DOCS_SDK} is NOT a published release on PyPI -- refusing to cut ${DOCS_TAG}." >&2
    echo "cut: fix content/api-reference/flyte-sdk/_index.md 'version:' (it should match a real flyte release)." >&2
    exit 1 ;;
  unknown)
    echo "cut: WARNING could not verify flyte-sdk ${DOCS_SDK:-?} on PyPI (network?) -- proceeding." >&2 ;;
esac

if [ "$AUTO" = "1" ] && [ "${DOCS_CUT_KIND}" != "sdk-release" ]; then
  echo "cut: --auto and not an sdk-release cut -- no-op (a manual cut is required)"
  exit 0
fi

if git rev-parse -q --verify "refs/tags/${DOCS_TAG}" >/dev/null; then
  echo "cut: tag ${DOCS_TAG} already exists -- no-op"
  exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "cut: --dry-run -- would write ${MANIFEST_REL}, commit, and tag ${DOCS_TAG}"
  exit 0
fi

# Materialize the combined manifest (all variants + the version decision).
REPO_ROOT="$REPO_ROOT" uv run --quiet "$TOOL" --write --variant both --out "$MANIFEST_REL"

# Commit the manifest on a detached HEAD and tag it, so `main` is not advanced.
ORIG_REF="$(git symbolic-ref -q --short HEAD || git rev-parse HEAD)"
BOT=(-c user.name="docsy-bot" -c user.email="noreply@union.ai")
git checkout -q --detach
git add "$MANIFEST_REL"
git "${BOT[@]}" commit -q -s \
    -m "cut ${DOCS_TAG}" -m "Docs version ${DOCS_DOCS_VERSION} (${DOCS_CUT_KIND})."
git "${BOT[@]}" tag -a "${DOCS_TAG}" -m "Docs version ${DOCS_DOCS_VERSION}"
git checkout -q "$ORIG_REF"

echo "cut: tagged ${DOCS_TAG} (manifest pinned; ${ORIG_REF} not advanced)"

if [ "$PUSH" = "1" ]; then
  git push origin "refs/tags/${DOCS_TAG}"
  echo "cut: pushed tag ${DOCS_TAG}"
else
  echo "cut: local tag only (pass --push to publish)"
fi
