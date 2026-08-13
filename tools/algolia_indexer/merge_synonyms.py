#!/usr/bin/env python3
"""Merge the generated and hand-seeded synonym sets into one pushable payload.

Synonyms come from two places that cannot be combined by hand at push time:

  synonyms.draft.json   GENERATED from the migration tables (build_synonyms.py).
                        A flat list, regenerable, safe to overwrite.
  synonyms.manual.json  HAND-SEEDED by investigation -- concept renames and
                        general/specific pairs that no table row maps. Grouped
                        into categories for review, so it is NOT a flat list
                        and cannot be fed to push_records directly.

Merging is not a convenience, it is REQUIRED. push_records applies synonyms with
replaceExistingSynonyms=true, which clears the index's synonym set and installs
the batch. Pushing either file alone therefore DELETES the other's synonyms.
There is no incremental path; the payload must always be complete.

Two things are excluded deliberately, and both are reported rather than dropped
silently:

  content_gaps_NOT_synonyms   queries with no page to find. A synonym cannot
                              fix a page that does not exist -- pushing these
                              would map a real query onto nothing.
  _-prefixed keys             provenance (_evidence, _found, _note) kept in the
                              source files for review. push_records strips
                              these too; doing it here keeps the merged file
                              readable as the exact payload.

Fails on an objectID collision rather than letting one entry silently win.

Usage:
    merge_synonyms.py --draft synonyms.draft.json --manual synonyms.manual.json \
        --out synonyms.merged.json
"""

import argparse
import json
import sys
from pathlib import Path

SKIP_KEYS = ("_README", "content_gaps_NOT_synonyms")
REQUIRED = ("objectID", "type")


def load_manual(path):
    """Flatten the categorised manual file, reporting what is skipped."""
    doc = json.loads(Path(path).read_text())
    if isinstance(doc, list):
        return doc, {}
    out, skipped = [], {}
    for key, val in doc.items():
        if not isinstance(val, list):
            continue
        if key in SKIP_KEYS:
            if key != "_README":
                skipped[key] = len(val)
            continue
        out.extend(val)
    return out, skipped


def strip_provenance(entry):
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def validate(entry, source):
    for field in REQUIRED:
        if not entry.get(field):
            raise SystemExit(f"{source}: entry missing '{field}': "
                             f"{json.dumps(entry)[:120]}")
    if entry["type"] == "oneWaySynonym":
        if not entry.get("input") or not entry.get("synonyms"):
            raise SystemExit(f"{source}: oneWaySynonym needs 'input' and "
                             f"'synonyms': {entry['objectID']}")
    elif entry["type"] == "synonym" and not entry.get("synonyms"):
        raise SystemExit(f"{source}: synonym needs 'synonyms': "
                         f"{entry['objectID']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", default="synonyms.draft.json")
    ap.add_argument("--manual", default="synonyms.manual.json")
    ap.add_argument("--out", default="synonyms.merged.json")
    args = ap.parse_args()

    draft = json.loads(Path(args.draft).read_text())
    manual, skipped = load_manual(args.manual)

    merged, seen = [], {}
    for entries, source in ((draft, args.draft), (manual, args.manual)):
        for entry in entries:
            validate(entry, source)
            oid = entry["objectID"]
            if oid in seen:
                raise SystemExit(
                    f"objectID collision: '{oid}' in both {seen[oid]} and "
                    f"{source}. Rename one -- a silent overwrite would make the "
                    f"pushed set depend on merge order.")
            seen[oid] = source
            merged.append(strip_provenance(entry))

    Path(args.out).write_text(json.dumps(merged, indent=2) + "\n",
                              encoding="utf-8")

    by_type = {}
    for e in merged:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    print(f"  generated (draft): {len(draft):>3}")
    print(f"  hand-seeded:       {len(manual):>3}")
    print(f"  merged:            {len(merged):>3}")
    for t, n in sorted(by_type.items()):
        print(f"    {t:<18} {n:>3}")
    for key, n in skipped.items():
        print(f"  EXCLUDED {key}: {n} (not synonyms -- no page to point at)")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
