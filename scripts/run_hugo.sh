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

# Version selector (DOC-1245): when versions.toml exists, drive the two-level version
# selector from it. Emits a structured `version_menu` (JSON) grouped by line (v2, v1),
# each line -> Latest (the line's bleeding edge, where one is served) / Stable (the
# bare line segment) / the numbered pins (newest first). A flat `versions` is also
# emitted for back-compat. No versions.toml (v1 single build / pre-go-live) -> neither
# is set and hugo.ver.toml's static ["v2","v1"] stands. Only for versioned builds.
if [[ -n $VERSION && -f "${REPO_ROOT:-.}/versions.toml" ]]; then
    # Append the params straight from python's stdout (no command substitution —
    # a heredoc inside $() plus single quotes trips macOS bash 3.2).
    python3 - "${REPO_ROOT:-.}/versions.toml" >> "$hugo_build_toml" <<'PY'
import sys, json
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
enum = set(d.get("enumerated", []))
def vkey(t):  # numeric-tuple sort, no third-party deps
    try:
        return tuple(int(x) for x in t.lstrip("v").split("."))
    except Exception:
        return ()
# Which lines to show, in top-level order. A line's "latest" segment is served only
# where a bleeding-edge build exists -- currently v2 (/docs/latest); v1 is frozen, so
# it shows Stable + pins until a v1 latest is served.
LINES = [("v2", "latest"), ("v1", None)]
menu, flat = [], []
for line, latest_seg in LINES:
    items = []
    if latest_seg:
        items.append({"seg": latest_seg, "label": "Latest", "channel": "latest"})
    items.append({"seg": line, "label": "Stable", "channel": "stable"})
    for p in sorted((p for p in enum if p.startswith(line + ".")), key=vkey, reverse=True):
        items.append({"seg": p, "label": p.lstrip("v"), "channel": "pin"})
    menu.append({"line": line, "items": items})
    flat += [it["seg"] for it in items]
print("versions = " + json.dumps(flat))
# JSON in a TOML single-quoted literal string (JSON uses only double quotes, so safe).
print("version_menu = '" + json.dumps(menu) + "'")
PY
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
