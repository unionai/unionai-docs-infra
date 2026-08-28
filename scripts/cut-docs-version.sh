#!/usr/bin/env bash
#
# Cut a docs version (DOC-1245, prds docs_versioning §9.4).
#
# Materializes the version, then tags v2.x.y.z. The tag pins the combined
# data/version-manifest.json (an immutable, reproducible snapshot incl. the resolved
# backend version, which the footer shows). INLINE-first: when the cut/regen PR folds
# the manifest into HEAD, the tag is placed ON the branch commit (so it's inline in
# history and the manifest is a normal tracked file); otherwise it falls back to a
# detached manifest commit. Either way we push ONLY the tag, never a branch. Pushing a
# branch is impossible here anyway (main/v1 are protected). /docs/latest regenerates
# its manifest at build time, so only pinned tags carry a frozen manifest.
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

# One-merge model (DOC-1245): versions.toml is the intent; a cut materializes the
# tag it names. If versions.toml pins a `stable`, it MUST equal what the resolver
# computes -- a mismatch means a hand-edited versions.toml (or a stale API-ref), so
# refuse rather than mint a tag nobody promoted.
if [ -f "$REPO_ROOT/versions.toml" ]; then
  STABLE="$(sed -n 's/^stable *= *"\(.*\)"/\1/p' "$REPO_ROOT/versions.toml" | head -1)"
  if [ -n "$STABLE" ] && [ "$STABLE" != "${DOCS_TAG}" ]; then
    echo "cut: versions.toml stable=${STABLE} but the resolver computes ${DOCS_TAG} -- refusing." >&2
    echo "cut: re-run 'manifest.py --promote' so versions.toml names the resolved tag." >&2
    exit 1
  fi
fi

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

BOT=(-c user.name="docsy-bot" -c user.email="noreply@union.ai")

# INLINE-first: if HEAD already carries the manifest for THIS cut (folded into the
# cut/regen PR alongside versions.toml), tag HEAD directly. The tag is then INLINE in
# the branch history (git describe / log --tags find it), and the manifest is a normal
# tracked file on the branch — no detached sidecar commit. We push ONLY the tag (never
# the branch), so branch protection is never touched. build_versions regenerates the
# manifest for the /docs/latest build, so latest's footer stays live while pinned tags
# keep their committed (backend-pinned) manifest.
if git cat-file -e "HEAD:${MANIFEST_REL}" 2>/dev/null && \
   git show "HEAD:${MANIFEST_REL}" | grep -q "\"docs_version\": \"${DOCS_DOCS_VERSION}\""; then
  git "${BOT[@]}" tag -a "${DOCS_TAG}" -m "Docs version ${DOCS_DOCS_VERSION}" HEAD
  echo "cut: tagged ${DOCS_TAG} INLINE on HEAD (manifest committed in the PR)"
else
  # Fallback (manifest not folded — a legacy or bare manual cut): materialize it and
  # commit on a DETACHED HEAD so the branch is not advanced (no branch push needed).
  REPO_ROOT="$REPO_ROOT" uv run --quiet "$TOOL" --write --variant both --out "$MANIFEST_REL"
  ORIG_REF="$(git symbolic-ref -q --short HEAD || git rev-parse HEAD)"
  git checkout -q --detach
  git add "$MANIFEST_REL"
  git "${BOT[@]}" commit -q -s \
      -m "cut ${DOCS_TAG}" -m "Docs version ${DOCS_DOCS_VERSION} (${DOCS_CUT_KIND})."
  git "${BOT[@]}" tag -a "${DOCS_TAG}" -m "Docs version ${DOCS_DOCS_VERSION}"
  git checkout -q "$ORIG_REF"
  echo "cut: tagged ${DOCS_TAG} detached (manifest not folded; ${ORIG_REF} not advanced)"
fi

if [ "$PUSH" = "1" ]; then
  git push origin "refs/tags/${DOCS_TAG}"
  echo "cut: pushed tag ${DOCS_TAG}"
else
  echo "cut: local tag only (pass --push to publish)"
fi
