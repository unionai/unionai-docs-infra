#!/bin/bash
# Assert the pin-retention window agrees across the three places that state it
# (DOC-1441).
#
# WHY THIS EXISTS
#
# A pinned version tree is only searchable if BOTH halves agree it is indexed:
#
#   tools/algolia_indexer/build_records.py           --keep-pins   (keyword index)
#   tools/algolia_indexer/build_markdown_records.py  --keep-pins   (Ask AI index)
#   layouts/partials/search.html                     $pinWindow    (the client)
#
# The builders decide which pins get records. The client decides which pins are
# filtered on their OWN facet rather than falling back to their line's stable.
#
# Drift fails in the worse direction. If the client's window is larger than the
# builders', it filters a reader on a pin to a facet value carrying no records
# and the search box comes back EMPTY -- the exact DOC-1408 failure the fallback
# exists to prevent. Nothing errors; the reader just sees no results.
#
# They cannot share a constant: the builders are standalone CLIs whose defaults
# must work when invoked by hand, and the client is a Hugo template with no way
# to read a Python argparse default. So the value lives in three places and this
# check turns divergence into a build failure instead of a silent empty box.
#
# Usage: scripts/check-pin-window-parity.sh    (exit 1 on any mismatch)
set -uo pipefail

cd "$(dirname "$0")/.."

fail=0
note() { printf '  %-46s %s\n' "$1" "$2"; }

kw=$(grep -A3 '"--keep-pins"' tools/algolia_indexer/build_records.py | grep -oE 'default=-?[0-9]+' | head -1 | cut -d= -f2)
md=$(grep -A3 '"--keep-pins"' tools/algolia_indexer/build_markdown_records.py | grep -oE 'default=-?[0-9]+' | head -1 | cut -d= -f2)
tpl=$(grep -oE '\$pinWindow := [0-9]+' layouts/partials/search.html | grep -oE '[0-9]+' | head -1)

echo "pin retention window:"
note "build_records.py --keep-pins"          "${kw:-<not found>}"
note "build_markdown_records.py --keep-pins" "${md:-<not found>}"
note "search.html \$pinWindow"                "${tpl:-<not found>}"

for v in "$kw" "$md" "$tpl"; do
  if [ -z "$v" ]; then
    echo "ERROR: could not extract one of the three values -- the check is blind, which is worse"
    echo "       than a mismatch. Fix the extraction in $0 before trusting a pass."
    exit 1
  fi
done

if [ "$kw" != "$md" ]; then
  echo "ERROR: the two indexers disagree ($kw vs $md). One index would carry pins the"
  echo "       other does not, so Ask AI and keyword search would cover different trees."
  fail=1
fi

if [ "$tpl" -gt "$kw" ] 2>/dev/null; then
  echo "ERROR: the client window ($tpl) is LARGER than the keyword indexer's ($kw)."
  echo "       A reader on a pin in that gap is filtered to a facet with no records and"
  echo "       gets an EMPTY search box -- the DOC-1408 failure, silently reintroduced."
  fail=1
elif [ "$tpl" != "$kw" ]; then
  echo "ERROR: the client window ($tpl) does not match the indexers' ($kw)."
  echo "       Smaller is not dangerous, but it silently sends readers on indexed pins"
  echo "       to their line's stable instead of the tree they are reading."
  fail=1
fi

[ "$fail" = 0 ] && echo "OK: all three agree ($kw)"
exit "$fail"
