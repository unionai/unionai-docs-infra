#!/bin/bash
# Assert the two version-menu implementations agree (DOC-1330).
#
# WHY THIS EXISTS
#
# The selector's entries for a line are built twice, on purpose:
#
#   run_hugo.sh        builds ALL lines' groups and bakes them into the page at
#                      build time (the initial render).
#   build_versions.sh  writes THIS line's group to /docs/<line>/versions.json, which
#                      sibling lines fetch at runtime to correct their stale copy
#                      (DOC-1330 defect 3 -- a page otherwise knows its sibling only
#                      as of its own last build).
#
# They cannot trivially share code: the first runs inside a per-version git worktree
# from a heredoc, the second in the assembling checkout. So the badge rules --
# latest -> LATEST, stable -> number + STABLE on the primary line but a bare number
# on a secondary one, older pins -> bare number, newest-first -- live in both.
#
# Divergence would be near-invisible in production: the baked menu and the fetched
# menu would simply disagree, and which one a visitor saw would depend on whether a
# fetch had completed. This check turns that into a build failure instead.
#
# Usage: scripts/check-version-menu-parity.sh    (exit 1 on any mismatch)
set -uo pipefail

cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Extract both implementations from their host scripts, so this checks the code that
# actually ships rather than a copy that could itself drift.
awk '/^    python3 - /{f=1;next} f&&/^PY$/{exit} f' scripts/run_hugo.sh        > "$tmp/menu.py"
awk '/^  python3 - /{f=1;next}   f&&/^PY$/{exit} f' scripts/build_versions.sh  > "$tmp/line.py"
for f in menu line; do
  if [ ! -s "$tmp/$f.py" ]; then
    echo "FAIL: could not extract the $f implementation -- did the heredoc indentation change?" >&2
    exit 1
  fi
done

# A stub git so the run_hugo derivation sees a controlled set of sibling branches.
mkdir -p "$tmp/bin"
cat > "$tmp/bin/git" <<'EOF'
#!/bin/bash
for a in "$@"; do
  case "$a" in origin/*:versions.toml)
    br="${a#origin/}"; br="${br%:versions.toml}"
    [ -f "$VT_DIR/$br.toml" ] && cat "$VT_DIR/$br.toml" && exit 0 || exit 1;;
  esac
done
exit 0
EOF
chmod +x "$tmp/bin/git"

fail=0

# case: <desc> | <line> | <stable> | <latest 1/0> | <enumerated, space-sep>
run_case() {
  local desc="$1" line="$2" stable="$3" latest="$4" enum="$5"

  # What build_versions.sh would publish for this line.
  local published
  published="$("$tmp/../bin/true" 2>/dev/null; python3 "$tmp/line.py" /dev/stdout \
                "$line" "$stable" "$latest" "$enum")" || { echo "FAIL($desc): writer errored" >&2; fail=1; return; }

  # What run_hugo.sh would bake for the same line. Feed it a versions.toml holding
  # exactly this line, with no siblings reachable.
  local enum_toml=""
  for e in $enum; do enum_toml="${enum_toml}\"$e\","; done
  { echo "stable = \"$stable\""
    echo "enumerated = [$enum_toml]"
    [ "$latest" = "1" ] || echo "latest = false"
  } > "$tmp/local.toml"

  local baked
  baked="$(VT_DIR="$tmp/empty" PATH="$tmp/bin:$PATH" python3 "$tmp/menu.py" "$tmp/local.toml" . \
           | python3 -c '
import sys, json
for l in sys.stdin:
    if l.startswith("version_menu"):
        m = json.loads(l.split("'"'"'",1)[1].rsplit("'"'"'",1)[0])
        for g in m:
            print(json.dumps(g, sort_keys=True)); break')" \
    || { echo "FAIL($desc): menu builder errored" >&2; fail=1; return; }

  local norm_pub
  norm_pub="$(printf '%s' "$published" | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin),sort_keys=True))')"

  if [ "$norm_pub" = "$baked" ]; then
    echo "  ok    $desc"
  else
    echo "  FAIL  $desc" >&2
    echo "        published: $norm_pub" >&2
    echo "        baked:     $baked" >&2
    fail=1
  fi
}

mkdir -p "$tmp/empty"
echo "Version-menu parity (baked vs published):"
run_case "primary line, cut with one pin"   v2 v2.5.16.1 1 "v2.5.16.0"
run_case "primary line, no pins yet"        v2 v2.5.16.0 1 ""
run_case "secondary line, cut with one pin" v1 v1.16.26.1 0 "v1.16.26.0"
run_case "secondary line, no pins yet"      v1 v1.16.26.0 0 ""
run_case "several pins, newest-first order"  v2 v2.6.0.0  1 "v2.5.16.0 v2.5.16.1 v2.4.0.0"

if [ "$fail" != 0 ]; then
  echo >&2
  echo "The baked menu and the published per-line manifest disagree. Both build the" >&2
  echo "same entries and MUST stay in step -- see the header of this script." >&2
  exit 1
fi
echo "All version-menu parity cases agree."
