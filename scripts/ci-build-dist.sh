#!/usr/bin/env bash
# The ONE CI build step, shared by push deploys AND PR previews (DOC-1333).
#
# WHY THIS EXISTS
#
# This logic used to live twice: inline in build-and-deploy.yml (push) and
# inline in build-pr.yml (PR previews). When docs versioning landed (DOC-1245),
# the versions.toml gate was added to the push copy ONLY — nobody decided that
# previews should stay single-version; the twin step just never got the edit.
# Result: every PR preview built a different artifact class than production
# (no /docs/latest, no pins, no versions.json, no _headers), which meant
# changes to the versioned assembly were unexercisable pre-merge — discovered
# when infra#192's preview "verified" against SPA-stub 200s (DOC-1333).
#
# Both workflows now call THIS script, so the build logic cannot silently
# diverge again. The workflows differ only in the MODE they ask for.
#
# Usage: ci-build-dist.sh <mode>
#
#   auto      versions.toml present -> full multi-version assembly; else the
#             single-version `make dist`. What push deploys use — identical to
#             the previous inline behaviour.
#   versions  force the full assembly (falls back to single, loudly, if
#             versions.toml is absent — a branch without versioning has
#             nothing to assemble). What PR previews use when the PR touches
#             a versioning surface; ~3x the build time of `single`.
#   single    force `make dist`. What PR previews use by default — most PRs
#             never touch the assembly, and the cheap build keeps preview
#             latency down. This IS a deliberate fidelity trade-off: such a
#             preview has no /docs/latest, no pinned trees, no versions.json.
#
# LATEST_REF is forwarded to build_versions.sh: push deploys pass the branch
# (origin/main / origin/v1) so a tag-triggered cut still builds latest from
# the branch tip; PR previews pass HEAD so /docs/latest shows the PR's own
# content — that is the thing a preview is for.
set -euo pipefail

MODE="${1:-auto}"

case "$MODE" in
  auto)
    if [ -f versions.toml ]; then MODE=versions; else MODE=single; fi
    ;;
  versions|single) ;;
  *) echo "ci-build-dist.sh: unknown mode '$MODE' (want auto|versions|single)" >&2; exit 2 ;;
esac

if [ "$MODE" = versions ] && [ ! -f versions.toml ]; then
  echo "WARNING: versions mode requested but no versions.toml — falling back to single-version build" >&2
  MODE=single
fi

if [ "$MODE" = versions ]; then
  echo "==> ci-build-dist: multi-version assembly (versions.toml present)"
  LATEST_REF="${LATEST_REF:-origin/main}" bash unionai-docs-infra/scripts/build_versions.sh
else
  echo "==> ci-build-dist: single-version build"
  make dist
fi
