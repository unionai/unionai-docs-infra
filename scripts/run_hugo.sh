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

# Version selector (DOC-1245): when versioning is on (versions.toml present), emit a
# structured `version_menu` (JSON grouped by line: v2, v1 -> Latest/Stable/pins) plus a
# flat `versions` for back-compat. The menu's SOURCE is the shared cross-line registry
# unionai-docs-infra/served-versions.toml when present (so every build lists both lines'
# versions); else the per-branch versions.toml (this line's pins by prefix + the other
# line's Stable). No versions.toml (v1 single build / pre-go-live) -> hugo.ver.toml's
# static ["v2","v1"] stands. Only for versioned builds (VERSION set).
if [[ -n $VERSION && -f "${REPO_ROOT:-.}/versions.toml" ]]; then
    _sel_src="${REPO_ROOT:-.}/unionai-docs-infra/served-versions.toml"
    [[ -f "$_sel_src" ]] || _sel_src="${REPO_ROOT:-.}/versions.toml"
    # Append the params straight from python's stdout (no command substitution --
    # a heredoc inside $() plus single quotes trips macOS bash 3.2).
    python3 - "$_sel_src" >> "$hugo_build_toml" <<'PY'
import sys, json
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
def vkey(t):  # numeric-tuple sort, no third-party deps
    try:
        return tuple(int(x) for x in t.lstrip("v").split("."))
    except Exception:
        return ()
LINE_ORDER = ["v2", "v1"]
# A line's bleeding-edge ("Latest") URL segment. v2 -> /docs/latest; v1 has none yet
# (frozen), so its Latest is skipped even if the registry flags it.
LATEST_SEG = {"v2": "latest"}
if any(ln in d for ln in LINE_ORDER):
    # Shared registry: explicit per-line [v2]/[v1] tables (latest flag + enumerated).
    def line_cfg(ln):
        c = d.get(ln, {})
        return bool(c.get("latest", ln == "v2")), list(c.get("enumerated", []))
else:
    # Per-branch versions.toml fallback: this line's pins by prefix; v2 has a latest.
    enum_all = list(d.get("enumerated", []))
    def line_cfg(ln):
        return (ln == "v2"), [p for p in enum_all if p.startswith(ln + ".")]
menu, flat = [], []
for ln in LINE_ORDER:
    has_latest, enum = line_cfg(ln)
    items = []
    if has_latest and LATEST_SEG.get(ln):
        items.append({"seg": LATEST_SEG[ln], "label": "Latest", "channel": "latest"})
    items.append({"seg": ln, "label": "Stable", "channel": "stable"})
    for p in sorted(set(enum), key=vkey, reverse=True):
        items.append({"seg": p, "label": p.lstrip("v"), "channel": "pin"})
    menu.append({"line": ln, "items": items})
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
