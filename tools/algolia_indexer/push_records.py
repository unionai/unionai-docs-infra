#!/usr/bin/env python3
"""Push generated search records to Algolia, scoped to the slices this build produced.

THE RULE THIS TOOL EXISTS TO ENFORCE:
  `main` builds only the v2 line; the `v1` branch builds only v1. Anything that
  rewrites the whole index from one branch would wipe the other line. So every
  write here is scoped to the (version, variant) slices actually present in
  records.json -- nothing outside them is ever touched.

  This is also why `replace_all_objects` is NOT used, despite being the obvious
  helper for a full refresh: it replaces the ENTIRE index, and this index is
  shared by every version and variant. A "full reconciliation" here is the same
  scoped sync run over a complete build -- the delete pass below already removes
  records for pages that disappeared.

Sync algorithm, per slice:
  1. browse the index for objectIDs currently carrying that (version, variant)
  2. upsert every record the build produced (idempotent)
  3. delete the leftovers -- pages that were removed or renamed

Credentials come from the environment, never from arguments:
  ALGOLIA_DOCS_2_APPLICATION_ID   (the new "unionai-docs-2" app)
  ALGOLIA_DOCS_2_WRITE_API_KEY

ALGOLIA_DOCS_1_* is the live prod app ("union.ai docs") and is never written to
by this tool.

Usage:
    push_records.py --records records.json --index union --dry-run
    push_records.py --records records.json --index union --settings settings.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from algoliasearch.search.client import SearchClientSync


def browse_slice_ids(client, index, version, variant):
    """objectIDs currently in the index for one (version, variant) slice."""
    if not client.index_exists(index):
        return set()

    ids = set()

    def collect(response):
        for hit in response.hits:
            oid = getattr(hit, "object_id", None) or hit.get("objectID")
            if oid:
                ids.add(oid)

    client.browse_objects(
        index_name=index,
        aggregator=collect,
        browse_params={
            "filters": f'version:"{version}" AND variant:"{variant}"',
            "attributesToRetrieve": ["objectID"],
            "hitsPerPage": 1000,
        },
    )
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", help="records.json from build_records.py")
    ap.add_argument("--prune",
                    help="prune.json from the builders: (version, variant) slices "
                         "to DELETE wholesale -- pins aged out of the --keep-pins "
                         "window, which are neither rebuilt nor otherwise removed.")
    ap.add_argument("--index", default="union")
    ap.add_argument("--settings", help="apply index settings from this JSON file")
    ap.add_argument("--synonyms", help="apply one-way synonyms from this JSON file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Read and cross-check the inputs BEFORE touching the network. A slice that
    # is both built and pruned means the builder is confused about its own
    # retention window, and continuing would delete records this same run just
    # wrote. Catching it here makes the failure instant, offline-testable, and
    # impossible to hit halfway through a push.
    records = json.loads(Path(args.records).read_text()) if args.records else []
    by_slice = defaultdict(list)
    for r in records:
        by_slice[(r["version"], r["variant"])].append(r)

    prune = json.loads(Path(args.prune).read_text()) if args.prune else []
    overlap = sorted({(p["version"], p["variant"]) for p in prune} & set(by_slice))
    if overlap:
        raise SystemExit(
            f"ERROR: {len(overlap)} slice(s) are marked for pruning AND present in "
            f"this build: {overlap}\n"
            f"       Refusing to run -- this would delete records just written.")

    app_id = os.environ.get("ALGOLIA_DOCS_2_APPLICATION_ID")
    api_key = os.environ.get("ALGOLIA_DOCS_2_WRITE_API_KEY")
    if not app_id or not api_key:
        raise SystemExit("set ALGOLIA_DOCS_2_APPLICATION_ID and "
                         "ALGOLIA_DOCS_2_WRITE_API_KEY")

    client = SearchClientSync(app_id, api_key)
    print(f"app {app_id}  index {args.index}"
          f"{'  [DRY RUN — no writes]' if args.dry_run else ''}\n")

    if args.settings:
        settings = json.loads(Path(args.settings).read_text())
        if args.dry_run:
            print(f"would apply {len(settings)} index settings")
        else:
            client.set_settings(index_name=args.index, index_settings=settings)
            print(f"applied {len(settings)} index settings")

    if args.synonyms:
        raw = json.loads(Path(args.synonyms).read_text())
        # Strip the _-prefixed provenance fields the generator adds for review.
        syns = [{k: v for k, v in s.items() if not k.startswith("_")} for s in raw]
        if args.dry_run:
            print(f"would apply {len(syns)} synonyms")
        else:
            client.save_synonyms(index_name=args.index, synonym_hit=syns,
                                 replace_existing_synonyms=True)
            print(f"applied {len(syns)} synonyms")

    if not args.records:
        if not (args.settings or args.synonyms or args.prune):
            raise SystemExit("nothing to do: pass --records, --settings or --synonyms")
        return 0

    print(f"\n{len(records)} records across {len(by_slice)} slice(s) in this build")
    print("slices NOT in this build are left untouched\n")

    tot_up = tot_del = 0
    for (version, variant), recs in sorted(by_slice.items()):
        new_ids = {r["objectID"] for r in recs}
        existing = browse_slice_ids(client, args.index, version, variant)
        stale = sorted(existing - new_ids)

        print(f"  {version}/{variant:<6} build={len(recs):>6}  "
              f"in-index={len(existing):>6}  upsert={len(recs):>6}  delete={len(stale):>5}")

        if not args.dry_run:
            # wait_for_tasks so a verification query straight after this run
            # reads the finished index rather than a half-applied one.
            client.save_objects(index_name=args.index, objects=recs,
                                wait_for_tasks=True)
            if stale:
                client.delete_objects(index_name=args.index, object_ids=stale,
                                      wait_for_tasks=True)

        tot_up += len(recs)
        tot_del += len(stale)

    # Aged-out pins. Deliberately NOT folded into the sync above: that loop is
    # scoped to slices present in this build, and widening it is what would let
    # one line delete another's records. This is a separate, explicit pass over
    # slices the builder named.
    tot_pruned = 0
    if prune:
        print(f"\npruning {len(prune)} aged-out slice(s):")
        for p in prune:
            version, variant = p["version"], p["variant"]
            stale = sorted(browse_slice_ids(client, args.index, version, variant))
            print(f"  {version}/{variant:<6} delete={len(stale):>6}")
            if stale and not args.dry_run:
                client.delete_objects(index_name=args.index, object_ids=stale,
                                      wait_for_tasks=True)
            tot_pruned += len(stale)

    verb, verb2 = ("would upsert", "would delete") if args.dry_run else ("upserted", "deleted")
    print(f"\n{verb} {tot_up}, {verb2} {tot_del}"
          + (f", pruned {tot_pruned} from aged-out pins" if tot_pruned else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
