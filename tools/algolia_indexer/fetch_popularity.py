#!/usr/bin/env python3
"""Refresh popularity.json -- observed clicks per docs page, for the pageRank prior.

build_records uses this as a weak popularity term in `weight.pageRank`. It is
regenerated deliberately, not on every deploy: click data moves slowly, and a
prior that changes under every build makes a ranking regression impossible to
attribute.

TWO THINGS THIS EXISTS TO GET RIGHT, both of which silently destroy the signal:

1. REGION. Analytics lives on a per-application region. `analytics.us.algolia.com`
   answers HTTP 200 with {"searches": null} / no hits for an app whose data is in
   `de` -- a silent empty answer that reads exactly like "this app has no traffic".
   Verified here via /2/status: a live region returns a non-null updatedAt.

2. REDIRECTS. Analytics records the URL the user clicked, which is the URL as it
   was THEN. When the docs are restructured those paths stop matching the current
   tree: on the first run, 103 of 152 clicked paths had moved and only 24% still
   resolved directly -- so a naive aggregation silently discarded three quarters
   of the signal. Every path is therefore resolved through the live site's
   redirects before being counted.

Output is keyed by version- and variant-free path
("user-guide/tasks/task-configuration/secrets"), matching what
build_records.page_rank looks up, so one page carries one prior across
v2/latest/pins and across union/flyte.

Usage:
    fetch_popularity.py --out popularity.json
    fetch_popularity.py --app-env ALGOLIA_DOCS_2 --days 30      # after cutover
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

TIMEOUT = 60
# Algolia's analytics endpoint 403s the default urllib User-Agent. curl works,
# which makes this look like a credentials problem when it is not -- the same
# key returns 200 from the shell and 403 from Python.
USER_AGENT = "unionai-docs-indexer/1.0"
SITE = "https://www.union.ai"
HIT_RE = re.compile(r"https://www\.union\.ai/docs/(v\d+|latest)/(union|flyte)/([^#?\s]*)")


def api(region, path, app_id, api_key):
    req = urllib.request.Request(
        f"https://analytics.{region}.algolia.com{path}",
        headers={"X-Algolia-Application-Id": app_id, "X-Algolia-API-Key": api_key,
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def live_region(index, app_id, api_key):
    """Pick the region that actually holds this app's data, and say which."""
    for region in ("de", "us"):
        try:
            if api(region, f"/2/status?index={index}", app_id, api_key).get("updatedAt"):
                return region
        except urllib.error.HTTPError:
            continue
    raise SystemExit("no analytics region returned a non-null updatedAt -- "
                     "check the index name and that the key carries the "
                     "analytics ACL (a plain search key 401s here)")


def resolve(path, cache):
    """Follow the live site's redirects to the page's CURRENT path."""
    if path in cache:
        return cache[path]
    out = subprocess.run(
        ["curl", "-sS", "-L", "-o", os.devnull, "-w", "%{url_effective} %{http_code}",
         f"{SITE}/docs/v2/union/{path}/"],
        capture_output=True, text=True, timeout=45).stdout.split()
    result = None
    if len(out) == 2 and out[1] == "200" and "/docs/v2/union/" in out[0]:
        result = out[0].split("/docs/v2/union/")[-1].strip("/")
    cache[path] = result
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).with_name("popularity.json")))
    ap.add_argument("--app-env", default="ALGOLIA_DOCS_1",
                    help="env prefix for the app whose analytics to read. Defaults "
                         "to the legacy app, which holds the history; switch to "
                         "ALGOLIA_DOCS_2 once the new app has accumulated its own.")
    ap.add_argument("--index", default="union")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--no-resolve", action="store_true",
                    help="skip redirect resolution (fast, but drops every path "
                         "that has moved -- see the module docstring)")
    args = ap.parse_args()

    app_id = os.environ.get(f"{args.app_env}_APPLICATION_ID")
    api_key = (os.environ.get(f"{args.app_env}_ANALYTICS_API_KEY")
               or os.environ.get(f"{args.app_env}_API_KEY"))
    if not app_id or not api_key:
        raise SystemExit(f"set {args.app_env}_APPLICATION_ID and "
                         f"{args.app_env}_ANALYTICS_API_KEY")

    region = live_region(args.index, app_id, api_key)
    end = date.today()
    start = end - timedelta(days=args.days)
    data = api(region,
               f"/2/hits?index={args.index}&limit={args.limit}"
               f"&startDate={start}&endDate={end}", app_id, api_key)
    hits = data.get("hits") or []

    raw = collections.Counter()
    for h in hits:
        m = HIT_RE.search(str(h.get("hit") or ""))
        if m:
            raw[m.group(3).strip("/")] += h.get("count", 0) or 0

    counts, cache, moved, dropped = collections.Counter(), {}, 0, 0
    for path, n in raw.items():
        if args.no_resolve:
            counts[path] += n
            continue
        current = resolve(path, cache)
        if current is None:
            dropped += n
            continue
        if current != path:
            moved += 1
        counts[current] += n

    Path(args.out).write_text(
        json.dumps(dict(counts.most_common()), indent=1) + "\n", encoding="utf-8")

    total = sum(raw.values())
    kept = sum(counts.values())
    print(f"  region {region}  |  {args.days}d  |  {len(hits)} analytics rows")
    print(f"  {len(raw)} clicked paths -> {len(counts)} live pages"
          f"  ({moved} had moved)")
    print(f"  clicks retained {kept} of {total}"
          f"{f' ({dropped} on pages that no longer resolve)' if dropped else ''}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
