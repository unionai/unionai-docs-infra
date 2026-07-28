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
# `version_menu` (JSON, grouped by line for the "v2 / v1" dividers, but rendered as ONE
# flat list) plus a flat `versions` for back-compat. Each line lists: latest (v2 only, a
# LATEST badge), stable (the newest tag, served at /docs/<line>; number + a STABLE badge),
# then the OLDER pins at /docs/<tag>. The newest tag is never a separate pin -- no dup tree.
# SOURCE: the shared cross-line registry unionai-docs-infra/served-versions.toml when
# present (so every build lists both lines); else the per-branch versions.toml (this line
# only). No versions.toml (v1 single build / pre-go-live) -> hugo.ver.toml's static
# ["v2","v1"] stands. Only for versioned builds (VERSION set).
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
# A line's bleeding-edge ("latest") URL segment. only v2 -> /docs/latest. That URL is global (the edge routes it to the v2
# deployment), so a secondary line (v1) has none -- skipped even if flagged.
LATEST_SEG = {"v2": "latest"}
if any(ln in d for ln in LINE_ORDER):
    # Shared registry: explicit per-line [v2]/[v1] tables (latest flag + stable tag +
    # enumerated OLDER pins).
    def line_cfg(ln):
        c = d.get(ln, {})
        return bool(c.get("latest", ln == "v2")), c.get("stable", ""), list(c.get("enumerated", []))
else:
    # Per-branch versions.toml fallback: only THIS line (stable + older pins, matched by
    # prefix); the cross-line registry is the real source for both lines.
    _stable = d.get("stable", "")
    _enum = list(d.get("enumerated", []))
    def line_cfg(ln):
        if not _stable.startswith(ln + "."):
            return (ln == "v2"), "", []
        return (ln == "v2"), _stable, [p for p in _enum if p.startswith(ln + ".")]
menu, flat = [], []
# Each item is {seg, num, badge}: `num` is the version number shown (empty for
# latest); `badge` is a real badge label ("LATEST"/"STABLE") or "" (older pins +
# a secondary line's newest (v1), which shows the bare number). The line token (v2/v1) is
# the group heading + the closed-state prefix.
for ln in LINE_ORDER:
    has_latest, stable, enum = line_cfg(ln)
    if not stable and not enum:
        continue                      # line not served -> no group
    items = []
    if has_latest and LATEST_SEG.get(ln):
        items.append({"seg": LATEST_SEG[ln], "num": "", "badge": "LATEST"})
    if stable:
        num = stable.lstrip("v")
        # Primary line (v2): /docs/<line> is the moving stable pointer -> number + STABLE badge.
        # Secondary line (v1): advances too, but deliberately de-emphasized -> bare number, no
        # badge (the numbered git tag still exists; we just do not surface STABLE in the menu).
        items.append({"seg": ln, "num": num, "badge": "STABLE" if has_latest else ""})
    for p in sorted(set(enum), key=vkey, reverse=True):
        items.append({"seg": p, "num": p.lstrip("v"), "badge": ""})
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
