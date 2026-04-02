#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Live redirect smoke test.

Reads redirects.csv, samples representative URLs from each pattern category,
hits the live site, and reports pass/fail.

Usage:
    uv run python unionai-docs-infra/tools/test_live_redirects.py
    uv run python unionai-docs-infra/tools/test_live_redirects.py --samples 5 --verbose
    uv run python unionai-docs-infra/tools/test_live_redirects.py --csv path/to/redirects.csv
"""

import argparse
import http.client
import random
import re
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _head_no_follow(url: str) -> tuple[int, str | None]:
    """HEAD request without following redirects."""
    parsed = urlparse(url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"

    if use_ssl:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=10)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=10)

    try:
        conn.request("HEAD", path, headers={
            "Host": host,
            "User-Agent": "unionai-redirect-test/1.0",
        })
        resp = conn.getresponse()
        location = resp.getheader("Location")
        return resp.status, location
    finally:
        conn.close()


def follow_redirects(url: str, max_hops: int = 10) -> list[dict]:
    """Follow a redirect chain, returning each hop."""
    hops = []
    current_url = url
    seen = set()
    for _ in range(max_hops):
        if current_url in seen:
            hops.append({"url": current_url, "status": None, "location": None,
                         "error": "redirect loop"})
            break
        seen.add(current_url)

        try:
            status, location = _head_no_follow(current_url)
        except Exception as e:
            hops.append({"url": current_url, "status": None, "location": None,
                         "error": str(e)})
            break

        hops.append({"url": current_url, "status": status, "location": location})

        if status in (301, 302, 307, 308) and location:
            if location.startswith("/"):
                parsed = urlparse(current_url)
                location = f"{parsed.scheme}://{parsed.hostname}{location}"
            current_url = location
        else:
            break

    return hops


# ── CSV loading ───────────────────────────────────────────────────────────────

def load_csv(path: Path) -> list[list[str]]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line.split(","))
    return rows


# ── Categorization ────────────────────────────────────────────────────────────

def categorize(rows: list[list[str]]) -> dict[str, list[list[str]]]:
    """Bucket redirects into pattern categories."""
    cats: dict[str, list[list[str]]] = {}

    for row in rows:
        src = row[0]
        subpath = row[4] == "TRUE" if len(row) > 4 else False

        if subpath:
            cat = "subpath-matching"
        elif src.startswith("docs.union.ai"):
            cat = "legacy-docs-union-ai"
        elif "/_r_/flyte" in src:
            cat = "flyte-chain"
        elif re.match(r"www\.union\.ai/docs/v2/byoc/deployment/", src):
            cat = "v2-byoc-deployment"
        elif re.match(r"www\.union\.ai/docs/v2/selfmanaged/deployment/", src):
            cat = "v2-selfmanaged-deployment"
        elif re.match(r"www\.union\.ai/docs/v2/serverless/", src):
            cat = "v2-serverless"
        elif re.match(r"www\.union\.ai/docs/v1/serverless/", src):
            cat = "v1-serverless"
        elif re.match(r"www\.union\.ai/docs/v2/union/", src):
            cat = "v2-union"
        elif re.match(r"www\.union\.ai/docs/v2/byoc/", src):
            cat = "v2-byoc"
        elif re.match(r"www\.union\.ai/docs/v2/flyte/", src):
            cat = "v2-flyte"
        elif re.match(r"www\.union\.ai/docs/v1/byoc/", src):
            cat = "v1-byoc"
        else:
            cat = "other"

        cats.setdefault(cat, []).append(row)

    return cats


# ── Test runner ───────────────────────────────────────────────────────────────

def test_redirect(src: str, expected_dest: str, verbose: bool) -> tuple[bool, str]:
    """Test a single redirect. Returns (passed, message)."""
    # Cloudflare maps docs.flyte.org/<path> -> www.union.ai/_r_/flyte/<path>
    # internally, so test the real user-facing URL.
    if src.startswith("www.union.ai/_r_/flyte/"):
        path = src.removeprefix("www.union.ai/_r_/flyte/")
        url = f"https://docs.flyte.org/{path}"
    else:
        url = f"https://{src}"
    hops = follow_redirects(url)

    if not hops:
        return False, f"no response from {url}"

    last = hops[-1]

    if last.get("error"):
        chain_str = " -> ".join(str(h.get("status", "?")) for h in hops)
        return False, f"error: {last['error']} (chain: {chain_str})"

    final_url = last["url"]
    final_status = last["status"]

    # Normalize for comparison
    expected_parsed = urlparse(expected_dest)
    final_parsed = urlparse(final_url)

    expected_path = expected_parsed.path.rstrip("/")
    final_path = final_parsed.path.rstrip("/")

    # The live site may insert a version prefix (e.g., /docs/byoc/... -> /docs/v1/byoc/...).
    # Accept if the final path matches with a version inserted after /docs/.
    def paths_match(expected: str, actual: str) -> bool:
        if expected == actual:
            return True
        # Try inserting /v1/ or /v2/ after /docs/
        for ver in ("v1", "v2"):
            versioned = re.sub(r"^/docs/", f"/docs/{ver}/", expected)
            if versioned == actual:
                return True
        return False

    matched = paths_match(expected_path, final_path)

    if matched and final_status in (200, 301, 302, 308):
        detail = ""
        if verbose:
            chain_str = " -> ".join(
                f"{h['status']}({urlparse(h['url']).path})" for h in hops
            )
            detail = f" [{chain_str}]"
        return True, f"{_short(src)} -> {_short(expected_dest)}{detail}"

    # Failure
    chain_str = " -> ".join(str(h.get("status", "?")) for h in hops)
    return False, (
        f"{_short(src)} -> expected {_short(expected_dest)}, "
        f"got {final_status} at {final_parsed.path} (chain: {chain_str})"
    )


def test_subpath(src: str, dest: str, suffix: str, verbose: bool) -> tuple[bool, str]:
    """Test a subpath-matching rule with a synthetic suffix."""
    test_src = f"{src}/{suffix}"
    expected = f"{dest}/{suffix}"
    return test_redirect(test_src, expected, verbose)


def _short(url: str) -> str:
    """Shorten URL for display."""
    return re.sub(r"https?://(www\.)?union\.ai", "", url)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Live redirect smoke test")
    parser.add_argument("--csv", default="unionai-docs-infra/redirects.csv",
                        help="Path to redirects.csv")
    parser.add_argument("--samples", type=int, default=3,
                        help="Samples per category (default: 3)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full redirect chains")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        sys.exit(1)

    rows = load_csv(csv_path)
    cats = categorize(rows)
    random.seed(args.seed)

    print(f"Testing live redirects ({args.samples} samples per category)...")
    print(f"CSV: {csv_path} ({len(rows)} redirects)")
    print(f"Categories: {', '.join(f'{k}({len(v)})' for k, v in sorted(cats.items()))}")
    print()

    total_pass = 0
    total_fail = 0

    # Test each category
    for cat_name in sorted(cats.keys()):
        cat_rows = cats[cat_name]

        # Subpath-matching gets special treatment
        if cat_name == "subpath-matching":
            suffixes = ["user-guide", "api-reference/flyte-cli", "deployment"]
            passed = 0
            failed = 0
            results = []

            for row in cat_rows:
                src, dest = row[0], row[1]
                for suffix in suffixes:
                    ok, msg = test_subpath(src, dest, suffix, args.verbose)
                    results.append((ok, msg))
                    if ok:
                        passed += 1
                    else:
                        failed += 1
                    time.sleep(0.3)

            total_pass += passed
            total_fail += failed
            status = "PASS" if failed == 0 else "FAIL"
            print(f"{cat_name} ({passed}/{passed + failed}) [{status}]")
            for ok, msg in results:
                mark = "+" if ok else "FAIL"
                print(f"  {mark} {msg}")
            print()
            continue

        # Sample from category
        sample = random.sample(cat_rows, min(args.samples, len(cat_rows)))

        passed = 0
        failed = 0
        results = []

        for row in sample:
            src, dest = row[0], row[1]
            ok, msg = test_redirect(src, dest, args.verbose)
            results.append((ok, msg))
            if ok:
                passed += 1
            else:
                failed += 1
            time.sleep(0.3)

        total_pass += passed
        total_fail += failed
        status = "PASS" if failed == 0 else "FAIL"
        print(f"{cat_name} ({passed}/{passed + failed}) [{status}]")
        for ok, msg in results:
            mark = "+" if ok else "FAIL"
            print(f"  {mark} {msg}")
        print()

    # Summary
    total = total_pass + total_fail
    if total_fail == 0:
        print(f"RESULT: {total_pass}/{total} passed")
        sys.exit(0)
    else:
        print(f"RESULT: {total_pass}/{total} passed, {total_fail} FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
