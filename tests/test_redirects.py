#!/usr/bin/env python3
"""
Comprehensive test suite for redirects.csv validation.

Two modes:
  Static (default)  — validates CSV format, invariants, and migration correctness.
  Live (--live)      — makes HTTP requests against the published site to verify
                       redirect behavior end-to-end.

Usage:
    uv run python tests/test_redirects.py                 # static tests only
    uv run python tests/test_redirects.py --live           # static + live tests
    uv run python tests/test_redirects.py --live-only      # live tests only
    uv run pytest tests/test_redirects.py -v               # via pytest (static only)
    uv run pytest tests/test_redirects.py -v --live        # via pytest (static + live)
"""

import argparse
import csv
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
import http.client
import ssl
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from _repo import INFRA_ROOT

REDIRECTS_FILE = INFRA_ROOT / "redirects.csv"

# Valid variants that should appear in destinations (serverless is discontinued)
ACTIVE_VARIANTS = {"flyte", "byoc", "selfmanaged"}
DISCONTINUED_VARIANTS = {"serverless"}
ALL_KNOWN_VARIANTS = ACTIVE_VARIANTS | DISCONTINUED_VARIANTS

# Valid source URL domains
VALID_SOURCE_DOMAINS = {"docs.union.ai", "www.union.ai"}

# Valid versions in URLs
VALID_VERSIONS = {"v1", "v2"}

# Expected CSV column count
EXPECTED_COLUMNS = 7

# Valid boolean values
VALID_BOOLS = {"TRUE", "FALSE"}

# Valid status codes
VALID_STATUS_CODES = {301, 302, 307, 308}

# Delay between live HTTP requests (seconds) to be respectful
LIVE_REQUEST_DELAY = 0.3

# Global flag set by --live / --live-only CLI arguments
_run_live_tests = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_rows() -> list[list[str]]:
    """Load all rows from redirects.csv."""
    rows = []
    with open(REDIRECTS_FILE, "r", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if row and not all(cell.strip() == "" for cell in row):
                rows.append(row)
    return rows


def parse_source_url(source: str) -> dict:
    """Parse a source URL into its components.

    Returns dict with keys: domain, version, variant, path (all optional).
    """
    parts = source.split("/", 3)
    result = {"domain": parts[0] if parts else "", "raw": source}

    if len(parts) >= 2 and parts[1] == "docs":
        # www.union.ai/docs/... format
        if len(parts) >= 3 and parts[2] in VALID_VERSIONS:
            result["version"] = parts[2]
            rest = parts[3] if len(parts) >= 4 else ""
            rest_parts = rest.split("/", 1)
            if rest_parts[0] in ALL_KNOWN_VARIANTS:
                result["variant"] = rest_parts[0]
                result["path"] = rest_parts[1] if len(rest_parts) > 1 else ""
            else:
                result["path"] = rest
        else:
            rest = "/".join(parts[2:])
            result["path"] = rest
    elif len(parts) >= 2 and parts[1] == "_r_":
        # www.union.ai/_r_/flyte/... format (docs.flyte.org chain)
        result["chain"] = "_r_"
        if len(parts) >= 3:
            result["variant"] = parts[2]
            result["path"] = parts[3] if len(parts) >= 4 else ""
    elif result["domain"] == "docs.union.ai":
        # Legacy docs.union.ai/... format (no /docs/ prefix, no version)
        rest = "/".join(parts[1:])
        rest_parts = rest.split("/", 1)
        if rest_parts[0] in ALL_KNOWN_VARIANTS:
            result["variant"] = rest_parts[0]
            result["path"] = rest_parts[1] if len(rest_parts) > 1 else ""
        else:
            result["path"] = rest

    return result


def parse_dest_url(dest: str) -> dict:
    """Parse a destination URL into its components."""
    parsed = urlparse(dest)
    path_parts = parsed.path.strip("/").split("/")

    result = {"scheme": parsed.scheme, "domain": parsed.hostname or "", "raw": dest}

    if len(path_parts) >= 1 and path_parts[0] == "docs":
        if len(path_parts) >= 2 and path_parts[1] in VALID_VERSIONS:
            result["version"] = path_parts[1]
            if len(path_parts) >= 3 and path_parts[2] in ALL_KNOWN_VARIANTS:
                result["variant"] = path_parts[2]
                result["path"] = "/".join(path_parts[3:])
            else:
                result["path"] = "/".join(path_parts[2:])
        elif len(path_parts) >= 2 and path_parts[1] in ALL_KNOWN_VARIANTS:
            result["variant"] = path_parts[1]
            result["path"] = "/".join(path_parts[2:])
        else:
            result["path"] = "/".join(path_parts[1:])

    return result


def _head_no_follow(url: str) -> tuple[int, str | None]:
    """Make a HEAD request without following redirects.

    Returns (status_code, location_header_or_None).
    """
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
    """Follow a redirect chain, returning each hop.

    Uses raw HTTP to avoid urllib's automatic redirect following.
    Returns list of dicts: [{url, status, location}, ...].
    The final entry has the terminal status (200, 404, etc.) and no location.
    """
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
            # Handle relative redirects
            if location.startswith("/"):
                parsed = urlparse(current_url)
                location = f"{parsed.scheme}://{parsed.hostname}{location}"
            current_url = location
        else:
            break

    return hops


# ---------------------------------------------------------------------------
# Static tests — CSV format and invariants
# ---------------------------------------------------------------------------

class TestCSVFormat:
    """Tests for CSV file format compliance."""

    def test_file_exists(self):
        assert REDIRECTS_FILE.exists(), f"redirects.csv not found at {REDIRECTS_FILE}"

    def test_file_not_empty(self):
        rows = load_rows()
        assert len(rows) > 0, "redirects.csv is empty"

    def test_all_rows_have_7_columns(self):
        rows = load_rows()
        bad_rows = []
        for i, row in enumerate(rows, 1):
            if len(row) != EXPECTED_COLUMNS:
                bad_rows.append((i, len(row), row[0] if row else "<empty>"))
        assert not bad_rows, (
            f"{len(bad_rows)} rows have wrong column count:\n"
            + "\n".join(f"  line {n}: {cols} cols (source: {src})" for n, cols, src in bad_rows[:10])
        )

    def test_no_empty_source_urls(self):
        rows = load_rows()
        empty = [i for i, row in enumerate(rows, 1) if not row[0].strip()]
        assert not empty, f"Empty source URLs at lines: {empty[:10]}"

    def test_no_empty_destination_urls(self):
        rows = load_rows()
        empty = [i for i, row in enumerate(rows, 1) if not row[1].strip()]
        assert not empty, f"Empty destination URLs at lines: {empty[:10]}"

    def test_valid_status_codes(self):
        rows = load_rows()
        bad = []
        for i, row in enumerate(rows, 1):
            try:
                code = int(row[2].strip())
                if code not in VALID_STATUS_CODES:
                    bad.append((i, code, row[0]))
            except ValueError:
                bad.append((i, row[2], row[0]))
        assert not bad, (
            f"{len(bad)} rows have invalid status codes:\n"
            + "\n".join(f"  line {n}: code={c} (source: {s})" for n, c, s in bad[:10])
        )

    def test_boolean_columns_valid(self):
        """Columns 4-7 must be TRUE or FALSE."""
        rows = load_rows()
        bad = []
        col_names = ["include_subdomains", "subpath_matching",
                     "preserve_query_string", "preserve_path_suffix"]
        for i, row in enumerate(rows, 1):
            for col_idx, col_name in zip(range(3, 7), col_names):
                val = row[col_idx].strip().upper()
                if val not in VALID_BOOLS:
                    bad.append((i, col_name, val, row[0]))
        assert not bad, (
            f"{len(bad)} rows have invalid boolean values:\n"
            + "\n".join(f"  line {n}: {name}={v} (source: {s})"
                        for n, name, v, s in bad[:10])
        )

    def test_no_trailing_whitespace_in_urls(self):
        rows = load_rows()
        bad = [(i, row[0]) for i, row in enumerate(rows, 1)
               if row[0] != row[0].strip() or row[1] != row[1].strip()]
        assert not bad, f"{len(bad)} rows have whitespace in URLs: {bad[:10]}"

    def test_no_blank_lines(self):
        with open(REDIRECTS_FILE, "r") as f:
            for i, line in enumerate(f, 1):
                assert line.strip(), f"Blank line at line {i}"

    def test_no_header_row(self):
        rows = load_rows()
        first = rows[0]
        assert "source" not in first[0].lower(), f"Possible header row: {first}"
        assert "target" not in first[1].lower(), f"Possible header row: {first}"


class TestURLFormat:
    """Tests for URL format correctness."""

    def test_source_urls_have_no_scheme(self):
        rows = load_rows()
        bad = [(i, row[0]) for i, row in enumerate(rows, 1)
               if row[0].startswith("http://") or row[0].startswith("https://")]
        assert not bad, (
            f"{len(bad)} source URLs have scheme prefix:\n"
            + "\n".join(f"  line {n}: {s}" for n, s in bad[:10]))

    def test_destination_urls_have_https(self):
        rows = load_rows()
        bad = [(i, row[1]) for i, row in enumerate(rows, 1)
               if not row[1].startswith("https://")]
        assert not bad, (
            f"{len(bad)} destination URLs missing https://:\n"
            + "\n".join(f"  line {n}: {d}" for n, d in bad[:10]))

    def test_source_urls_have_valid_domain(self):
        rows = load_rows()
        bad = [(i, row[0].split("/")[0], row[0]) for i, row in enumerate(rows, 1)
               if row[0].split("/")[0] not in VALID_SOURCE_DOMAINS]
        assert not bad, (
            f"{len(bad)} source URLs have unexpected domains:\n"
            + "\n".join(f"  line {n}: domain={d}" for n, d, _ in bad[:10]))

    def test_destination_urls_domain(self):
        rows = load_rows()
        bad = []
        for i, row in enumerate(rows, 1):
            parsed = urlparse(row[1])
            if parsed.hostname != "www.union.ai":
                bad.append((i, parsed.hostname, row[1]))
        assert not bad, (
            f"{len(bad)} destination URLs point to wrong domain:\n"
            + "\n".join(f"  line {n}: {d}" for n, d, _ in bad[:10]))

    def test_no_double_slashes_in_paths(self):
        rows = load_rows()
        bad = []
        for i, row in enumerate(rows, 1):
            if "//" in row[0]:
                bad.append((i, "source", row[0]))
            dest_path = row[1].replace("https://", "")
            if "//" in dest_path:
                bad.append((i, "dest", row[1]))
        assert not bad, (
            f"{len(bad)} URLs contain //:\n"
            + "\n".join(f"  line {n}: {t}: {u}" for n, t, u in bad[:10]))

    def test_no_query_strings_in_source(self):
        rows = load_rows()
        bad = [(i, row[0]) for i, row in enumerate(rows, 1) if "?" in row[0]]
        assert not bad, (
            f"{len(bad)} source URLs contain query strings:\n"
            + "\n".join(f"  line {n}: {s}" for n, s in bad[:10]))


class TestRedirectInvariants:
    """Tests for redirect logical correctness."""

    def test_no_self_redirects(self):
        rows = load_rows()
        bad = [(i, row[0]) for i, row in enumerate(rows, 1)
               if row[0].lower() == row[1].lower().replace("https://", "")]
        assert not bad, (
            f"{len(bad)} self-redirects found:\n"
            + "\n".join(f"  line {n}: {s}" for n, s in bad[:10]))

    def test_no_duplicate_sources(self):
        rows = load_rows()
        sources = [row[0] for row in rows]
        dupes = {s: c for s, c in Counter(sources).items() if c > 1}
        assert not dupes, (
            f"{len(dupes)} duplicate source URLs:\n"
            + "\n".join(f"  {s} ({c}x)" for s, c in sorted(dupes.items())[:10]))

    def test_no_redirect_chains_within_csv(self):
        """No destination URL (stripped of https://) should also appear as a source.

        Note: This only checks for chains within the CSV itself. The
        docs.flyte.org -> www.union.ai/_r_/flyte chain is an out-of-repo
        redirect and is tested separately in the live tests.
        """
        rows = load_rows()
        sources = {row[0] for row in rows}
        chains = []
        for i, row in enumerate(rows, 1):
            dest_as_source = row[1].replace("https://", "")
            if dest_as_source in sources:
                chains.append((i, row[0], dest_as_source))
        assert not chains, (
            f"{len(chains)} in-CSV redirect chains (dest is also a source):\n"
            + "\n".join(f"  line {n}: {s} -> {d}" for n, s, d in chains[:10]))


class TestCloudflareConstraints:
    """Tests for Cloudflare Bulk Redirect API constraints.

    Note: The deploy_redirects.py script silently fixes the
    preserve_path_suffix/subpath_matching constraint at deploy time,
    so we only warn here rather than fail.
    """

    def test_preserve_path_suffix_vs_subpath_matching(self):
        """Warn if preserve_path_suffix=TRUE with subpath_matching=FALSE.

        Cloudflare rejects this combination. deploy_redirects.py handles it
        by silently setting preserve_path_suffix=FALSE at deploy time, but
        the CSV should ideally be consistent.
        """
        rows = load_rows()
        count = sum(
            1 for row in rows
            if row[6].strip().upper() == "TRUE" and row[4].strip().upper() == "FALSE"
        )
        if count > 0:
            print(
                f"\n  INFO: {count} rows have preserve_path_suffix=TRUE with "
                f"subpath_matching=FALSE (deploy_redirects.py fixes this silently)")

    def test_source_urls_not_too_long(self):
        rows = load_rows()
        max_len = 2048
        bad = [(i, len(row[0]), row[0][:80]) for i, row in enumerate(rows, 1)
               if len(row[0]) > max_len]
        assert not bad, f"{len(bad)} source URLs exceed {max_len} chars"


class TestServerlessMigration:
    """Tests specific to the serverless-to-BYOC redirect migration."""

    def test_no_serverless_in_destinations(self):
        """No destination URL should contain /serverless/."""
        rows = load_rows()
        bad = [(i, row[1]) for i, row in enumerate(rows, 1)
               if "/serverless/" in row[1] or row[1].endswith("/serverless")]
        assert not bad, (
            f"{len(bad)} destinations still contain /serverless/:\n"
            + "\n".join(f"  line {n}: {d}" for n, d in bad[:10]))

    def test_serverless_sources_redirect_to_byoc(self):
        """Every source with /serverless/ should redirect to /byoc/."""
        rows = load_rows()
        bad = []
        for i, row in enumerate(rows, 1):
            src = parse_source_url(row[0])
            dst = parse_dest_url(row[1])
            if src.get("variant") == "serverless" and dst.get("variant") != "byoc":
                bad.append((i, row[0], row[1], dst.get("variant")))
        assert not bad, (
            f"{len(bad)} serverless sources don't redirect to BYOC:\n"
            + "\n".join(f"  line {n}: {s} -> {d} (variant: {v})"
                        for n, s, d, v in bad[:10]))

    def test_serverless_v2_key_pages(self):
        """Key v2 serverless pages must have redirect entries."""
        rows = load_rows()
        sources = {row[0] for row in rows}
        # Leaf pages generated from .md files get trailing slash from URL generation
        expected = [
            "www.union.ai/docs/v2/serverless/api-reference/flyte-cli/",
            "www.union.ai/docs/v2/serverless/api-reference/flyte-context/",
        ]
        missing = [p for p in expected if p not in sources]
        assert not missing, f"Missing v2 serverless redirect entries: {missing}"

    def test_serverless_v1_key_pages(self):
        """Key v1 serverless pages must have redirect entries."""
        rows = load_rows()
        sources = {row[0] for row in rows}
        expected = [
            "www.union.ai/docs/v1/serverless/api-reference/flyte-context/",
        ]
        missing = [p for p in expected if p not in sources]
        assert not missing, f"Missing v1 serverless redirect entries: {missing}"

    def test_legacy_serverless_redirects_exist(self):
        rows = load_rows()
        legacy = [row for row in rows if row[0].startswith("docs.union.ai/serverless")]
        assert len(legacy) > 0, "No legacy docs.union.ai/serverless redirects found"

    def test_serverless_version_consistency(self):
        """v2 serverless sources should redirect to v2 byoc, v1 to v1."""
        rows = load_rows()
        bad = []
        for i, row in enumerate(rows, 1):
            src = parse_source_url(row[0])
            dst = parse_dest_url(row[1])
            if src.get("variant") != "serverless":
                continue
            src_ver = src.get("version")
            dst_ver = dst.get("version")
            if src_ver and dst_ver and src_ver != dst_ver:
                bad.append((i, row[0], row[1], src_ver, dst_ver))
        assert not bad, (
            f"{len(bad)} serverless redirects have version mismatch:\n"
            + "\n".join(f"  line {n}: {s} ({sv}) -> {d} ({dv})"
                        for n, s, d, sv, dv in bad[:10]))

    def test_new_serverless_entries_preserve_path(self):
        """Newly added serverless->BYOC entries should preserve the content path.

        Reports path mismatches (expected for historical renames) as INFO,
        does not fail.
        """
        rows = load_rows()
        mismatches = []
        for i, row in enumerate(rows, 1):
            src = parse_source_url(row[0])
            dst = parse_dest_url(row[1])
            if (src.get("variant") == "serverless"
                    and src.get("version")
                    and dst.get("variant") == "byoc"
                    and src.get("domain") == "www.union.ai"):
                sp = src.get("path", "").rstrip("/")
                dp = dst.get("path", "").rstrip("/")
                if sp != dp:
                    mismatches.append((i, sp, dp))
        if mismatches:
            print(
                f"\n  INFO: {len(mismatches)} serverless->byoc entries have "
                f"different paths (expected for historical renames):")
            for n, sp, dp in mismatches[:5]:
                print(f"    line {n}: {sp} -> {dp}")


class TestNonServerlessRedirects:
    """Ensure non-serverless redirects were not affected by the migration."""

    def test_non_serverless_entries_present(self):
        rows = load_rows()
        count = sum(1 for row in rows if "serverless" not in row[0])
        assert count > 100, (
            f"Only {count} non-serverless redirects — some may have been lost")

    def test_flyte_r_chain_entries_present(self):
        """The docs.flyte.org chain entries (www.union.ai/_r_/flyte/...) should exist."""
        rows = load_rows()
        r_entries = [row for row in rows if "/_r_/flyte" in row[0]]
        assert len(r_entries) > 100, (
            f"Only {len(r_entries)} _r_/flyte entries — expected >100")


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_root_serverless_redirects(self):
        """Root serverless pages (trailing slash) should redirect to byoc."""
        rows = load_rows()
        roots = [row for row in rows if row[0].endswith("serverless/")]
        for row in roots:
            assert "byoc" in row[1], (
                f"Root redirect {row[0]} doesn't point to byoc: {row[1]}")

    def test_404_page_redirects(self):
        rows = load_rows()
        fours = [row for row in rows if "serverless/404" in row[0]]
        for row in fours:
            assert "byoc/404" in row[1], (
                f"404 redirect wrong: {row[0]} -> {row[1]}")

    def test_api_reference_redirects(self):
        rows = load_rows()
        api = [row for row in rows
               if "serverless" in row[0] and "api-reference" in row[0]]
        assert len(api) > 10, (
            f"Only {len(api)} serverless API reference redirects — too few")
        for row in api:
            assert "/serverless/" not in row[1], (
                f"API dest still has serverless: {row[0]} -> {row[1]}")

    def test_deeply_nested_paths(self):
        rows = load_rows()
        deep = [row for row in rows
                if "serverless" in row[0] and row[0].count("/") > 7]
        for row in deep:
            assert row[1].startswith("https://"), (
                f"Deep path missing https: {row[1]}")
            assert "/serverless/" not in row[1], (
                f"Deep path still has serverless: {row[0]} -> {row[1]}")

    def test_special_characters_in_paths(self):
        """Source URLs should contain only safe URL characters."""
        bad_char = re.compile(r'[^a-zA-Z0-9\-_\.\/\:\@\,]')
        rows = load_rows()
        bad = [(i, row[0][:80], set(bad_char.findall(row[0])))
               for i, row in enumerate(rows, 1) if bad_char.search(row[0])]
        assert not bad, (
            f"{len(bad)} source URLs contain special characters:\n"
            + "\n".join(f"  line {n}: {u} (chars: {c})" for n, u, c in bad[:10]))


class TestCoverage:
    """Tests for redirect coverage completeness."""

    def test_total_serverless_entries(self):
        rows = load_rows()
        count = sum(1 for row in rows if "serverless" in row[0])
        assert count > 1000, (
            f"Only {count} serverless entries — expected >1000 (v1+v2+legacy)")

    def test_v2_serverless_coverage(self):
        rows = load_rows()
        v2 = [r for r in rows if r[0].startswith("www.union.ai/docs/v2/serverless")]
        assert len(v2) > 400, f"Only {len(v2)} v2 serverless redirects — expected >400"

    def test_v1_serverless_coverage(self):
        rows = load_rows()
        v1 = [r for r in rows if r[0].startswith("www.union.ai/docs/v1/serverless")]
        assert len(v1) > 600, f"Only {len(v1)} v1 serverless redirects — expected >600"

    def test_legacy_serverless_coverage(self):
        rows = load_rows()
        legacy = [r for r in rows if r[0].startswith("docs.union.ai/serverless")]
        assert len(legacy) > 90, (
            f"Only {len(legacy)} legacy serverless redirects — expected >90")


# ---------------------------------------------------------------------------
# Live HTTP tests — run with --live or --live-only
# ---------------------------------------------------------------------------

class TestLiveRedirects:
    """End-to-end HTTP redirect tests against the live published site.

    These tests make real HTTP requests. Run with --live to include them.

    NOTE: These tests validate the DEPLOYED state. If the new redirects CSV
    has not yet been deployed, serverless->byoc redirects will not yet work.
    The tests report this clearly rather than failing opaquely.
    """

    def _check(self, url: str, expect_dest_contains: str,
               expect_status: int = 302, label: str = "") -> dict:
        """Follow a URL and check the final destination.

        Returns a result dict for reporting.
        """
        time.sleep(LIVE_REQUEST_DELAY)
        hops = follow_redirects(url)
        result = {
            "url": url,
            "label": label,
            "hops": hops,
            "ok": False,
            "detail": "",
        }

        if not hops:
            result["detail"] = "No response"
            return result

        final = hops[-1]
        # Check the full chain: we should end up at a URL containing
        # expect_dest_contains
        chain_urls = [h["url"] for h in hops]
        if h := final.get("location"):
            chain_urls.append(h)

        # The destination we care about is the last URL in the chain
        terminal_url = chain_urls[-1]

        if expect_dest_contains in terminal_url:
            result["ok"] = True
            result["detail"] = f"-> {terminal_url}"
        else:
            result["detail"] = (
                f"Expected destination to contain '{expect_dest_contains}', "
                f"got: {terminal_url}"
            )
            # Add full chain for debugging
            result["detail"] += "\n      Chain: " + " -> ".join(
                f"{h['status']} {h['url']}" for h in hops
            )

        return result

    def test_live_serverless_v2_to_byoc(self):
        """v2 serverless URLs should redirect to v2 byoc equivalents."""
        if not _run_live_tests:
            return

        test_cases = [
            ("https://www.union.ai/docs/v2/serverless/user-guide/",
             "/docs/v2/byoc/user-guide", "user-guide root"),
            ("https://www.union.ai/docs/v2/serverless/api-reference/flyte-cli",
             "/docs/v2/byoc/api-reference/flyte-cli", "API reference leaf"),
            ("https://www.union.ai/docs/v2/serverless/api-reference/flyte-context",
             "/docs/v2/byoc/api-reference/flyte-context", "flyte-context page"),
        ]

        results = [self._check(url, dest, label=label)
                    for url, dest, label in test_cases]
        failures = [r for r in results if not r["ok"]]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"    {status}  [{r['label']}] {r['detail']}")

        assert not failures, (
            f"{len(failures)}/{len(results)} v2 serverless redirects failed "
            "(redirects may not be deployed yet)")

    def test_live_serverless_v1_to_byoc(self):
        """v1 serverless URLs should redirect to v1 byoc equivalents."""
        if not _run_live_tests:
            return

        test_cases = [
            ("https://www.union.ai/docs/v1/serverless/api-reference/flyte-context/",
             "/docs/v1/byoc/api-reference/flyte-context", "v1 flyte-context"),
        ]

        results = [self._check(url, dest, label=label)
                    for url, dest, label in test_cases]
        failures = [r for r in results if not r["ok"]]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"    {status}  [{r['label']}] {r['detail']}")

        assert not failures, (
            f"{len(failures)}/{len(results)} v1 serverless redirects failed")

    def test_live_legacy_serverless(self):
        """Legacy docs.union.ai/serverless/ URLs should land on byoc."""
        if not _run_live_tests:
            return

        test_cases = [
            ("https://docs.union.ai/serverless/administration",
             "/byoc/", "legacy admin"),
            ("https://docs.union.ai/serverless/api",
             "/byoc/", "legacy api"),
        ]

        results = [self._check(url, dest, label=label)
                    for url, dest, label in test_cases]
        failures = [r for r in results if not r["ok"]]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"    {status}  [{r['label']}] {r['detail']}")

        assert not failures, (
            f"{len(failures)}/{len(results)} legacy serverless redirects failed")

    def test_live_docs_flyte_org_chain(self):
        """docs.flyte.org/{path} should chain through _r_/flyte to final dest.

        Chain: docs.flyte.org/{path}
               -> www.union.ai/_r_/flyte/{path}  (out-of-repo redirect)
               -> final destination               (in-repo redirect from CSV)

        The chain may include an additional version redirect (e.g., /docs/flyte/
        -> /docs/v1/flyte/) added by the hosting layer.
        """
        if not _run_live_tests:
            return

        test_cases = [
            ("https://docs.flyte.org/",
             "union.ai/docs", "flyte root"),  # flexible: lands somewhere under /docs/
        ]

        results = [self._check(url, dest, label=label)
                    for url, dest, label in test_cases]
        failures = [r for r in results if not r["ok"]]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"    {status}  [{r['label']}] {r['detail']}")

        assert not failures, (
            f"{len(failures)}/{len(results)} docs.flyte.org chain tests failed")

    def test_live_non_serverless_unaffected(self):
        """Spot-check that existing non-serverless redirects still work."""
        if not _run_live_tests:
            return

        test_cases = [
            ("https://docs.union.ai/",
             "www.union.ai/docs", "docs.union.ai root"),
        ]

        results = [self._check(url, dest, label=label)
                    for url, dest, label in test_cases]
        failures = [r for r in results if not r["ok"]]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"    {status}  [{r['label']}] {r['detail']}")

        assert not failures, (
            f"{len(failures)}/{len(results)} non-serverless redirects failed")

    def test_live_random_sample(self):
        """Test a random sample of serverless redirect entries from the CSV."""
        if not _run_live_tests:
            return

        rows = load_rows()
        serverless_rows = [
            row for row in rows
            if "serverless" in row[0] and row[0].startswith("www.union.ai/docs/v2/")
        ]

        sample_size = min(5, len(serverless_rows))
        sample = random.sample(serverless_rows, sample_size)

        results = []
        for row in sample:
            url = f"https://{row[0]}"
            # The destination should contain /byoc/
            result = self._check(url, "/byoc/", label=row[0][-50:])
            results.append(result)

        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"    {status}  [{r['label']}] {r['detail']}")

        failures = [r for r in results if not r["ok"]]
        assert not failures, (
            f"{len(failures)}/{len(results)} random serverless samples failed "
            "(redirects may not be deployed yet)")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_tests(include_live: bool = False, live_only: bool = False):
    """Run test classes and report results."""
    global _run_live_tests
    _run_live_tests = include_live or live_only

    static_classes = [
        TestCSVFormat,
        TestURLFormat,
        TestRedirectInvariants,
        TestCloudflareConstraints,
        TestServerlessMigration,
        TestNonServerlessRedirects,
        TestEdgeCases,
        TestCoverage,
    ]
    live_classes = [
        TestLiveRedirects,
    ]

    if live_only:
        test_classes = live_classes
    elif include_live:
        test_classes = static_classes + live_classes
    else:
        test_classes = static_classes

    total = 0
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        methods = sorted(
            m for m in dir(instance)
            if m.startswith("test_") and callable(getattr(instance, m))
        )
        print(f"\n{'=' * 60}")
        print(f"  {cls.__name__} ({len(methods)} tests)")
        print(f"{'=' * 60}")

        for method_name in methods:
            total += 1
            method = getattr(instance, method_name)
            short_name = method_name.replace("test_", "")
            try:
                method()
                passed += 1
                print(f"  PASS  {short_name}")
            except AssertionError as e:
                failed += 1
                errors.append((cls.__name__, method_name, str(e)))
                first_line = str(e).split("\n")[0][:100]
                print(f"  FAIL  {short_name}: {first_line}")
            except Exception as e:
                failed += 1
                errors.append((cls.__name__, method_name, str(e)))
                print(f"  ERROR {short_name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'=' * 60}")

    if errors:
        print("\nFailed tests:")
        for cls_name, method_name, error in errors:
            print(f"\n  {cls_name}.{method_name}:")
            for line in error.split("\n")[:5]:
                print(f"    {line}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test redirects.csv")
    parser.add_argument("--live", action="store_true",
                        help="Include live HTTP tests against the published site")
    parser.add_argument("--live-only", action="store_true",
                        help="Run only live HTTP tests")
    args = parser.parse_args()
    sys.exit(run_tests(include_live=args.live, live_only=args.live_only))
