#!/bin/bash
# Assert the Hugo build is reproducible: build each variant twice from the same
# source and require the two trees to be byte-identical.
#
# Why (DOC-1357): nothing else in CI asserts reproducibility, so a build-varying
# id can sit in the output for months. It did -- headings containing a shortcode
# were getting their anchor from Hugo's internal shortcode placeholder, whose
# counter is assigned per build, so deep links into those headings broke on every
# deploy. Nondeterminism also hides regressions: diffing built output is the
# standard way to verify an infra change here, and background churn drowns the
# signal (a 4-file change once produced a 168-file diff).
#
# The one known-legitimate difference is the `?v=<timestamp>` cache-buster Hugo
# stamps on asset URLs, which is normalized away before comparing.
#
# Usage: unionai-docs-infra/scripts/check-determinism.sh [variant ...]
#   Defaults to the VARIANTS the repo declares in makefile.inc.
# Run from the docs repo root (unionai-docs), not from unionai-docs-infra.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$PWD}"
export REPO_ROOT

if [[ $# -gt 0 ]]; then
    variants=("$@")
else
    # shellcheck disable=SC2207
    variants=($(sed -n 's/^VARIANTS *:= *//p' "$REPO_ROOT/makefile.inc"))
fi

if [[ ${#variants[@]} -eq 0 ]]; then
    echo "check-determinism: could not determine the variant list" >&2
    exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

normalize() {
    # Strip the asset cache-buster, the one difference that is expected to move.
    find "$1" -type f \( -name '*.html' -o -name '*.xml' -o -name '*.json' \
        -o -name '*.md' -o -name '*.txt' \) -print0 \
        | xargs -0 -r perl -pi -e 's/\?v=\d+/?v=NORMALIZED/g'
}

status=0

for variant in "${variants[@]}"; do
    echo "==> determinism check: $variant"
    for pass in a b; do
        rm -rf "$REPO_ROOT/dist"
        if ! VERSION="" VARIANT="$variant" "$SCRIPT_DIR/run_hugo.sh" > "$work/build.$variant.$pass.log" 2>&1; then
            echo "FATAL: hugo build failed (variant=$variant, pass=$pass)" >&2
            tail -40 "$work/build.$variant.$pass.log" >&2
            exit 1
        fi
        mv "$REPO_ROOT/dist/docs/$variant" "$work/$variant-$pass"
        normalize "$work/$variant-$pass"
    done

    if diff -rq "$work/$variant-a" "$work/$variant-b" > "$work/diff.$variant" 2>&1; then
        echo "    OK - two builds are identical"
    else
        status=1
        count=$(wc -l < "$work/diff.$variant" | tr -d ' ')
        echo "FATAL: the build is not reproducible for variant '$variant'."
        echo "       $count path(s) differ between two builds of identical source."
        echo ""
        sed 's|'"$work"'/||g; s/^/  /' "$work/diff.$variant" | head -50
        [[ $count -gt 50 ]] && echo "  ... ($((count - 50)) more)"
        echo ""
        first=$(head -1 "$work/diff.$variant" | sed -E 's/^Files (.*) and (.*) differ$/\1|\2/')
        if [[ "$first" == *"|"* ]]; then
            echo "  first differing file, line by line:"
            diff "${first%%|*}" "${first##*|}" | head -20 | sed 's/^/    /'
            echo ""
        fi
        echo "  A heading or an element id derived from Hugo's internal shortcode"
        echo "  placeholder is the usual cause. See DOC-1357 and the render-heading /"
        echo "  render-codeblock hooks in unionai-docs-infra/layouts/_default/_markup/."
    fi
done

rm -rf "$REPO_ROOT/dist"
exit $status
