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

# Asset cache-buster (DOC-1309): ONE value for the whole build.
#
# baseof.html used to compute this as `now.Format`, which Hugo evaluates per PAGE
# render. A build takes ~25-30s, so pages emitted different stamps (…195646,
# …195705, …195708, …195709) and every CSS/JS file ended up cached under roughly
# as many URLs as the build had seconds. Navigating between two pages rendered in
# different seconds re-downloaded all 26 stylesheets — the cache was busted
# continuously rather than once per deploy.
#
# Passing it as a site param fixes that: identical for every page in this build,
# new on the next one, so a deploy still invalidates cleanly. BUILD comes from the
# Makefile (`BUILD := $(shell date +%s)`, shared with gen_404.sh); we fall back to
# our own timestamp so a direct run of this script is still correct.
if ! grep -q '^\[params\]' "$hugo_build_toml" 2>/dev/null; then
    echo '[params]' >> "$hugo_build_toml"
fi
echo "build_id = \"${BUILD:-$(date +%s)}\"" >> "$hugo_build_toml"

# Version selector (DOC-1245): when versioning is on (versions.toml present), emit a
# `version_menu` (JSON, grouped by line for the "v2 / v1" dividers, but rendered as ONE
# flat list) plus a flat `versions` for back-compat. Each line lists: latest (v2 only, a
# LATEST badge), stable (the newest tag, served at /docs/<line>; number + a STABLE badge),
# then the OLDER pins at /docs/<tag>. The newest tag is never a separate pin -- no dup tree.
# SOURCE (DOC-1330): DERIVED from every line's own versions.toml -- this line's from the
# working tree, the other lines' from origin. There is no hand-maintained cross-line
# registry any more.
#
# There used to be one: unionai-docs-infra/served-versions.toml, a file restating both
# lines' stable+enumerated so any page could list both. It was a cache with no
# invalidation. The cut workflow runs in unionai-docs and cannot write to the infra repo,
# so every cut needed a companion hand-edit over there, and the first cut after go-live
# (v2.5.16.1, docs#1324) duly shipped a page whose footer said 2.5.16.1 while its selector
# said 2.5.16.0 -- and whose freshly-pinned /docs/v2.5.16.0 tree was built but missing from
# the menu, because `enumerated` had not been rotated either.
#
# The data was never actually missing: each branch's versions.toml is authoritative for its
# own line and IS updated by the cut (that is why the footer was right). So read the
# originals instead of a copy of them.
#
# No versions.toml (v1 single build / pre-go-live) -> hugo.ver.toml's static ["v2","v1"]
# stands. Only for versioned builds (VERSION set).
if [[ -n $VERSION && -f "${REPO_ROOT:-.}/versions.toml" ]]; then
    _sel_src="${REPO_ROOT:-.}/versions.toml"
    # The other lines live on refs this checkout may not have -- CI checkouts are commonly
    # shallow or single-branch, and build_versions.sh runs us inside a linked worktree. A
    # linked worktree shares the superproject's object store, so a fetch here is visible to
    # `git show` below. Best-effort: if it fails (offline, no remote), the derivation below
    # degrades to listing only the lines it can actually see rather than failing the build.
    git -C "${REPO_ROOT:-.}" fetch -q --no-tags origin main v1 2>/dev/null || true
    # Append the params straight from python's stdout (no command substitution --
    # a heredoc inside $() plus single quotes trips macOS bash 3.2).
    python3 - "$_sel_src" "${REPO_ROOT:-.}" >> "$hugo_build_toml" <<'PY'
import sys, json, subprocess
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import re

# ---------------------------------------------------------------------------
# Build the cross-line registry (DOC-1330) instead of reading a hand-kept copy.
#
# `d` ends up in exactly the shape the old served-versions.toml provided --
# {"v2": {latest, stable, enumerated}, "v1": {...}} -- so everything downstream
# is untouched.
# ---------------------------------------------------------------------------
_local, _root = sys.argv[1], sys.argv[2]

def _line_of(cfg):
    """('v2', {...}) from a parsed versions.toml. The line is the stable tag's
    prefix -- v2.5.16.1 -> v2 -- so there is no separate field to keep in sync."""
    stable = cfg.get("stable", "")
    return stable.split(".")[0], {
        "latest": bool(cfg.get("latest", True)),
        "stable": stable,
        "enumerated": list(cfg.get("enumerated", [])),
    }

d = {}
# THIS line comes from the WORKING TREE, and is inserted FIRST so the setdefault
# below can never displace it. That ordering is load-bearing: during a cut PR the
# working tree holds the new number (v2.5.16.1) while origin/main still holds the
# old one (v2.5.16.0). Reading origin for the current line would overwrite the new
# value with the stale one and reintroduce the very bug this replaced -- only now
# automatically, on every cut, with no hand-edit left to blame.
_ln, _cfg = _line_of(tomllib.load(open(_local, "rb")))
if _ln:
    d[_ln] = _cfg

# The OTHER lines come from origin: they are separate branches, never checked out
# here. Iterating both branches unconditionally is safe -- whichever one IS the
# current line hits the setdefault and is skipped.
for _br in ("main", "v1"):
    try:
        _raw = subprocess.run(
            ["git", "-C", _root, "show", f"origin/{_br}:versions.toml"],
            capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        continue          # ref absent (shallow clone / pre-go-live branch) -> skip
    try:
        _o_ln, _o_cfg = _line_of(tomllib.loads(_raw))
    except Exception:
        continue          # unparseable versions.toml on that branch -> skip
    if _o_ln:
        d.setdefault(_o_ln, _o_cfg)
def vkey(t):  # numeric-tuple sort, no third-party deps
    try:
        return tuple(int(x) for x in t.lstrip("v").split("."))
    except Exception:
        return ()
def line_key(ln):  # "v2" -> 2, to order lines newest-major-first
    try:
        return int(ln[1:])
    except Exception:
        return 0
# The line list AND which line owns /docs/latest are DERIVED, not hardcoded. Adding a new
# major line (v3) is: create the branch with its own versions.toml (latest=true) and set
# the old primary's latest=false. Nothing here changes -- the loop above discovers it.
#
# One place still hardcodes the line list and is NOT derived: hugo.ver.toml's
# `versions = ["v2","v1"]`, which `make dev` reads (the dev config chain bypasses this
# script entirely, so nothing here overrides it there). Production builds override it via
# $hugo_build_toml below. So a v3 line also needs adding there, or {{< docs_home v3 >}}
# warns in local dev while production stays silent. Tracked in DOC-1330.
#
# The latest segment is always the global "/docs/latest", owned by whichever line has
# latest=true.
#
# `d` is now always the per-line shape (built above), so there is no second code path: the
# old "per-branch fallback" branch existed only for when served-versions.toml was absent
# and `d` was a bare {stable, enumerated} table. That file is gone and `d` is constructed,
# so the fallback was unreachable and has been removed.
LINE_ORDER = sorted([k for k in d if re.match(r"^v\d+$", k)], key=line_key, reverse=True)
def line_cfg(ln):
    c = d.get(ln, {})
    return bool(c.get("latest", False)), c.get("stable", ""), list(c.get("enumerated", []))
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
    if has_latest:
        items.append({"seg": "latest", "num": "", "badge": "LATEST"})
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
