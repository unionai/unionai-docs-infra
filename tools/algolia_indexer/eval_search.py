#!/usr/bin/env python3
"""Compare search quality across candidate index configurations.

Runs a labelled query set against N indices in the PRE-PROD app and reports
per-config metrics, so a settings change is chosen on evidence rather than
intuition. Prod is never touched: the frontend keeps pointing at the old app
until a winner is picked and promoted.

Query set format (queries.json):
  [
    {"q": "task environment",
     "ideal": ["/docs/v2/union/user-guide/get-started/core-concepts/task-environment/"],
     "note": "canonical concept page"},
    ...
  ]
`ideal` holds URL suffixes; a hit counts if its url_without_anchor ends with one.

Metrics:
  MRR@10        rank of the first ideal hit (1.0 = always first)
  Recall@5      share of queries with an ideal hit in the top 5
  nDCG@10       rank-weighted relevance
  Pages@10      distinct pages in the top 10 -- the dilution measure. Low
                values mean one page is flooding the result list.
  Zero          share of queries returning nothing

Usage:
    eval_search.py --queries queries.json --index union_ml6_page union_ml6_url
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


def search(app_id, api_key, index, query, hits=DEPTH):
    payload = json.dumps({
        "query": query,
        "hitsPerPage": hits,
        "attributesToRetrieve": ["url", "url_without_anchor"],
        "facetFilters": ["version:v2", "variant:union"],
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


def evaluate(app_id, api_key, index, queries):
    mrr = ndcg = recall5 = zero = pages = 0.0
    per_query = []

    for item in queries:
        hits = search(app_id, api_key, index, item["q"])
        ideals = item["ideal"]
        if not hits:
            zero += 1
            per_query.append((item["q"], None))
            continue

        pages += len({h.get("url_without_anchor") for h in hits})

        rank = next((i + 1 for i, h in enumerate(hits) if matches(h, ideals)), None)
        if rank:
            mrr += 1.0 / rank
            if rank <= 5:
                recall5 += 1
            ndcg += 1.0 / math.log2(rank + 1)
        per_query.append((item["q"], rank))

    n = len(queries)
    return {
        "MRR@10": mrr / n,
        "Recall@5": recall5 / n,
        "nDCG@10": ndcg / n,
        "Pages@10": pages / n,
        "Zero": zero / n,
    }, per_query


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--index", nargs="+", required=True,
                    help="one or more index names to compare")
    ap.add_argument("--per-query", action="store_true",
                    help="show the rank of the first ideal hit, per query")
    args = ap.parse_args()

    app_id = os.environ.get("ALGOLIA_DOCS_2_APPLICATION_ID")
    api_key = (os.environ.get("ALGOLIA_DOCS_2_SEARCH_API_KEY")
               or os.environ.get("ALGOLIA_DOCS_2_WRITE_API_KEY"))
    if not app_id or not api_key:
        raise SystemExit("set ALGOLIA_DOCS_2_APPLICATION_ID and a key")

    queries = json.load(open(args.queries))
    print(f"app {app_id}   {len(queries)} queries   depth {DEPTH}\n")

    results = {}
    per_q = {}
    for index in args.index:
        results[index], per_q[index] = evaluate(app_id, api_key, index, queries)

    cols = ["MRR@10", "Recall@5", "nDCG@10", "Pages@10", "Zero"]
    width = max(len(i) for i in args.index) + 2
    print(f"{'index':<{width}}" + "".join(f"{c:>11}" for c in cols))
    print("-" * (width + 11 * len(cols)))
    for index in args.index:
        row = results[index]
        print(f"{index:<{width}}" + "".join(f"{row[c]:>11.3f}" for c in cols))

    print("\nPages@10: distinct pages in the top 10. Higher is better —"
          "\n          a low value means one page floods the results.")

    if args.per_query:
        print("\nrank of first ideal hit (— = not found):")
        for i, item in enumerate(queries):
            ranks = "  ".join(
                f"{index}:{(per_q[index][i][1] or '—')!s:>3}" for index in args.index
            )
            print(f"  {item['q'][:44]:<46} {ranks}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
