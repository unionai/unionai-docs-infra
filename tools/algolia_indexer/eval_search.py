#!/usr/bin/env python3
"""Compare search quality across candidate index configurations.

Runs the labelled query set against N indices in the PRE-PROD app and reports
per-config metrics, so a settings change is chosen on evidence rather than
intuition. Prod is never touched: the frontend keeps pointing at the old app
until a winner is picked and promoted.

Query set format (queries.judged.json):
  {"_meta": {...},
   "queries": [
     {"q": "secret",
      "ideal": {"guide":     ["/docs/v2/union/user-guide/.../secrets/"],
                "reference": ["/docs/v2/union/api-reference/.../secret/"],
                "tutorial":  [],
                "other":     []}},
     ...]}

`ideal` is keyed BY FACET GROUP, and each group holds URL suffixes; a hit
counts if its url_without_anchor ends with one. An empty list is a real
answer -- "nothing in this group should match" -- and is excluded from the
ranking metrics rather than scored as a miss.

Two views, because they answer different questions:

  per-group   Within the group's own result list, how well is it ranked?
              This is what the grouped UI actually shows.
  flat        Does ANY labelled page for the query appear in the single
              unfiltered top 10? This is the "I typed a word and looked"
              experience, and it is the one that degrades invisibly when a
              group floods the list.

Metrics:
  MRR@10        rank of the first ideal hit (1.0 = always first)
  Recall@5      share of cases with an ideal hit in the top 5
  nDCG@10       rank-weighted relevance
  Pages@10      distinct pages in the top 10 -- the dilution measure. Low
                values mean one page is flooding the result list.
  Zero          share of queries returning nothing

Absolute scores are NOT a quality claim: the answer key carries judgment
error, so these are valid for comparing arms against each other -- where a
shared key makes systematic error cancel -- not as a statement about how
good search is.

Usage:
    eval_search.py --queries queries.judged.json --index union
    eval_search.py --queries queries.judged.json --index union --per-query
"""

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 30
DEPTH = 10
GROUPS = ("guide", "reference", "tutorial", "other")


def group_of(category):
    """Map an index `category` onto the four judged groups.

    `deployment` is judged under 'other' -- mirrors find_candidates.group_of,
    which is what the answer key was built with. Keeping these in sync matters:
    if they diverge, deployment pages score as permanent misses.
    """
    return "other" if category in ("deployment", "other", None, "") else category


def search(app_id, api_key, index, query, facets, hits=DEPTH):
    payload = json.dumps({
        "query": query,
        "hitsPerPage": hits,
        "attributesToRetrieve": ["url", "url_without_anchor", "category"],
        "facetFilters": facets,
    }).encode()
    req = urllib.request.Request(
        f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/query",
        data=payload,
        method="POST",
        headers={
            "X-Algolia-Application-Id": app_id,
            "X-Algolia-API-Key": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read()).get("hits", [])
    except urllib.error.HTTPError as e:
        raise SystemExit(f"search failed on {index}: {e.code} "
                         f"{e.read().decode(errors='replace')[:200]}")


def matches(hit, ideals):
    url = hit.get("url_without_anchor") or hit.get("url") or ""
    return any(url.rstrip("/").endswith(i.rstrip("/")) for i in ideals)


def rank_of(hits, ideals):
    """1-based rank of the first hit matching any ideal, or None."""
    return next((i + 1 for i, h in enumerate(hits) if matches(h, ideals)), None)


def score(rank, acc):
    acc["n"] += 1
    if rank:
        acc["mrr"] += 1.0 / rank
        acc["ndcg"] += 1.0 / math.log2(rank + 1)
        if rank <= 5:
            acc["recall5"] += 1


def new_acc():
    return {"n": 0, "mrr": 0.0, "ndcg": 0.0, "recall5": 0.0}


def finish(acc):
    n = acc["n"] or 1
    return {"MRR@10": acc["mrr"] / n, "Recall@5": acc["recall5"] / n,
            "nDCG@10": acc["ndcg"] / n, "n": acc["n"]}


def evaluate(app_id, api_key, index, queries, facets):
    flat = new_acc()
    per_group = {g: new_acc() for g in GROUPS}
    zero = 0
    pages = 0.0
    per_query = []

    for item in queries:
        ideal = item.get("ideal") or {}
        if not isinstance(ideal, dict):
            raise SystemExit(
                "queries file has a flat 'ideal' list; this eval expects the "
                "per-facet schema {group: [urls]}. Re-judge, or use an older "
                "eval revision.")

        hits = search(app_id, api_key, index, item["q"], facets)
        all_ideals = [u for urls in ideal.values() for u in urls]

        if not hits:
            zero += 1
            if all_ideals:
                score(None, flat)
            per_query.append((item["q"], None))
            continue

        pages += len({h.get("url_without_anchor") for h in hits})

        # flat view: any labelled page anywhere in the unfiltered top 10
        r_flat = rank_of(hits, all_ideals) if all_ideals else None
        if all_ideals:
            score(r_flat, flat)
        per_query.append((item["q"], r_flat))

        # per-group view: rank within that group's own slice of the list
        for g in GROUPS:
            if not ideal.get(g):
                continue  # [] is "nothing should match here", not a miss
            g_hits = [h for h in hits if group_of(h.get("category")) == g]
            score(rank_of(g_hits, ideal[g]), per_group[g])

    n = len(queries)
    return {
        "flat": {**finish(flat), "Pages@10": pages / n, "Zero": zero / n},
        "groups": {g: finish(per_group[g]) for g in GROUPS},
    }, per_query


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--index", nargs="+", required=True,
                    help="one or more index names to compare")
    ap.add_argument("--facets", nargs="*",
                    default=["version:v2", "variant:union"],
                    help="facetFilters applied to every search")
    ap.add_argument("--per-query", action="store_true",
                    help="show the rank of the first ideal hit, per query")
    args = ap.parse_args()

    app_id = os.environ.get("ALGOLIA_DOCS_2_APPLICATION_ID")
    api_key = (os.environ.get("ALGOLIA_DOCS_2_SEARCH_API_KEY")
               or os.environ.get("ALGOLIA_DOCS_2_WRITE_API_KEY"))
    if not app_id or not api_key:
        raise SystemExit("set ALGOLIA_DOCS_2_APPLICATION_ID and a key")

    doc = json.load(open(args.queries))
    queries = doc["queries"] if isinstance(doc, dict) else doc
    meta = doc.get("_meta", {}) if isinstance(doc, dict) else {}

    print(f"app {app_id}   {len(queries)} queries   depth {DEPTH}")
    print(f"facets {' '.join(args.facets) or '(none)'}")
    if meta.get("content_sha"):
        print(f"key judged at content {meta['content_sha'][:10]}")
    print()

    results, per_q = {}, {}
    for index in args.index:
        results[index], per_q[index] = evaluate(
            app_id, api_key, index, queries, args.facets)

    cols = ["MRR@10", "Recall@5", "nDCG@10", "Pages@10", "Zero"]
    width = max(len(i) for i in args.index) + 2
    print("FLAT — any labelled page in the single unfiltered top 10")
    print(f"{'index':<{width}}" + "".join(f"{c:>11}" for c in cols))
    print("-" * (width + 11 * len(cols)))
    for index in args.index:
        r = results[index]["flat"]
        print(f"{index:<{width}}" + "".join(f"{r[c]:>11.3f}" for c in cols))

    gcols = ["MRR@10", "Recall@5", "nDCG@10"]
    print("\nPER GROUP — rank within that group's own result list")
    for index in args.index:
        print(f"  {index}")
        print(f"    {'group':<12}{'n':>5}" + "".join(f"{c:>11}" for c in gcols))
        for g in GROUPS:
            r = results[index]["groups"][g]
            if not r["n"]:
                print(f"    {g:<12}{0:>5}          —          —          —")
                continue
            print(f"    {g:<12}{r['n']:>5}"
                  + "".join(f"{r[c]:>11.3f}" for c in gcols))

    print("\nPages@10: distinct pages in the top 10. Higher is better —"
          "\n          a low value means one page floods the results.")
    print("n:        labelled (query, group) pairs scored. Empty groups are"
          "\n          excluded: [] means 'nothing should match here'.")
    print("\nAbsolute scores are not a quality claim — the key carries judgment"
          "\nerror. Valid for comparing arms, where that error cancels.")

    if args.per_query:
        print("\nflat rank of first labelled hit (— = not found):")
        for i, item in enumerate(queries):
            ranks = "  ".join(
                f"{index}:{(per_q[index][i][1] or '—')!s:>3}"
                for index in args.index)
            print(f"  {item['q'][:44]:<46} {ranks}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
