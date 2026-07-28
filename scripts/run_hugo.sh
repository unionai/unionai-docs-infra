#!/bin/bash
set -o pipefail

declare -r hugo_build_toml=".hugo.build.${VARIANT}.toml"

trap 'rm -f "$hugo_build_toml"' EXIT

if [[ -z $VARIANT ]]; then
    echo "VARIANT is not set"
    exit 1
fi

declare target
declare baseURL

rm -f "$hugo_build_toml"

if [[ -z $VERSION ]]; then
    echo "Version LATEST"
    target="dist/docs/${VARIANT}"
    baseURL="/docs/${VARIANT}/"
    touch "$hugo_build_toml"
else
    echo "Version $VERSION"
    target="dist/docs/${VERSION}/${VARIANT}"
    baseURL="/docs/${VERSION}/${VARIANT}/"
    cat << EOF > "$hugo_build_toml"
[params]
current_version = "${VERSION}"
EOF
fi

# Version-scoped noindex (DOC-1245, docs-versioning A2 model): when NOINDEX=true,
# every page in this build emits robots=noindex,nofollow (via a site param the
# seo-meta.html partial reads). Used for /docs/latest (bleeding edge) and the
# immutable /docs/v2.x.y.z snapshots, so search concentrates on the one canonical
# stable surface, /docs/v2. See ROUTING-ARCHITECTURE.md.
if [[ "${NOINDEX:-}" == "true" ]]; then
    if ! grep -q '^\[params\]' "$hugo_build_toml" 2>/dev/null; then
        echo '[params]' >> "$hugo_build_toml"
    fi
    echo 'noindex = true' >> "$hugo_build_toml"
fi

# Version selector list (DOC-1245): when versions.toml exists, drive the dropdown
# from it (latest -> v2/stable -> each enumerated pin, newest first -> v1) instead
# of hugo.ver.toml's static ["v2","v1"]. This param overrides the static config, so
# the same list shows on every versioned build. No versions.toml (v1 single build /
# pre-go-live) -> the static list stands. Only for versioned builds (VERSION set).
if [[ -n $VERSION && -f "${REPO_ROOT:-.}/versions.toml" ]]; then
    versions_line="$(python3 - "${REPO_ROOT:-.}/versions.toml" <<'PY'
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
def _key(t):  # sort v2.5.12.0 by numeric tuple, no third-party deps
    try:
        return tuple(int(x) for x in t.lstrip("v").split("."))
    except Exception:
        return ()
pins = sorted(set(d.get("enumerated", [])), key=_key, reverse=True)
items = ["latest", "v2"] + pins + ["v1"]
print("versions = [" + ", ".join('"%s"' % i for i in items) + "]")
PY
)"
    echo "$versions_line" >> "$hugo_build_toml"
fi

readonly target

echo "Target: $target"

# Optional Hugo diagnostics (pass through from environment):
#   HUGO_METRICS=true  — --templateMetrics --templateMetricsHints
#   HUGO_VERBOSE=true  — --logLevel info --printPathWarnings --printMemoryUsage
hugo_extra_flags=""
[[ "$HUGO_METRICS" == "true" ]] && hugo_extra_flags+=" --templateMetrics --templateMetricsHints"
[[ "$HUGO_VERBOSE" == "true" ]] && hugo_extra_flags+=" --logLevel info --printPathWarnings --printMemoryUsage"

# --panicOnWarning makes all warnf calls fatal (not just errorf).
# This is intentional: content issues should block deployment.
hugo --config unionai-docs-infra/hugo.toml,unionai-docs-infra/hugo.site.toml,unionai-docs-infra/hugo.ver.toml,unionai-docs-infra/config.${VARIANT}.toml,${hugo_build_toml} \
    --destination "${target}" --baseURL "${baseURL}" \
    --noBuildLock --panicOnWarning $hugo_extra_flags

if [[ $? -ne 0 ]]; then
    echo "FATAL: Hugo build failed for variant=${VARIANT}"
    exit 1
fi
