#!/usr/bin/env python3
"""
Deploy redirects from redirects.csv to a Cloudflare Bulk Redirect List via API.

Usage:
    python deploy_redirects.py [--dry-run] [--csv PATH]

Reads redirects.csv, converts each row to the Cloudflare redirect item format,
and replaces all items in the configured Bulk Redirect List using:
    PUT /accounts/{account_id}/rules/lists/{list_id}/items

Environment variables (required unless --dry-run):
    CLOUDFLARE_API_TOKEN   - API token with "Account Filter Lists Edit" permission
    CLOUDFLARE_ACCOUNT_ID  - Cloudflare account identifier
    CLOUDFLARE_LIST_ID     - Bulk Redirect List identifier
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import INFRA_ROOT, get_repo_root

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

REDIRECTS_FILE = "redirects.csv"

#: Which branch carries the OTHER documentation line's versions.toml. Both must be
#: read before deploying -- see retired_pins().
SIBLING_BRANCH = {"v2": "v1", "v1": "main"}
CF_API_BASE = "https://api.cloudflare.com/client/v4"
POLL_INTERVAL_SECONDS = 2
POLL_MAX_ATTEMPTS = 60

#: Cloudflare's code for "you have been ratelimited please wait and try again".
#: Replacing this list is a bulk operation, and landing a change that touches both
#: lines can need several in a few minutes -- five in twenty minutes on 2026-08-26
#: hit this twice. Transient, so it is waited out rather than failed on.
RATE_LIMITED_CODE = 10040
#: Waits between attempts, seconds. The one that eventually succeeded on
#: 2026-08-26 was seven minutes after the last failure, so this is deliberately
#: patient: a redirect deploy that gives up leaves the live list stale, and the
#: symptom is 404s nobody connects to a CI failure days earlier.
RETRY_BACKOFF_SECONDS = (15, 45, 90, 180)


def parse_csv(csv_path: Path) -> list[dict]:
    """Parse redirects.csv into a list of Cloudflare redirect item dicts.

    CSV columns (no header row):
        0: source_url
        1: target_url
        2: status_code
        3: include_subdomains (TRUE/FALSE)
        4: subpath_matching (TRUE/FALSE)
        5: preserve_query_string (TRUE/FALSE)
        6: preserve_path_suffix (TRUE/FALSE)
    """
    items = []
    with open(csv_path, "r", newline="") as f:
        reader = csv.reader(f)
        for lineno, row in enumerate(reader, start=1):
            if not row or all(cell.strip() == "" for cell in row):
                continue
            if len(row) < 7:
                print(
                    f"Warning: skipping line {lineno}: expected 7 columns, got {len(row)}",
                    file=sys.stderr,
                )
                continue
            subpath_matching = row[4].strip().upper() == "TRUE"
            preserve_path_suffix = row[6].strip().upper() == "TRUE"
            # CF API rejects preserve_path_suffix when subpath_matching is off
            if preserve_path_suffix and not subpath_matching:
                preserve_path_suffix = False
            items.append(
                {
                    "redirect": {
                        "source_url": row[0].strip(),
                        "target_url": row[1].strip(),
                        "status_code": int(row[2].strip()),
                        "include_subdomains": row[3].strip().upper() == "TRUE",
                        "subpath_matching": subpath_matching,
                        "preserve_query_string": row[5].strip().upper() == "TRUE",
                        "preserve_path_suffix": preserve_path_suffix,
                    }
                }
            )
    return items



def _versions_from_branch(branch: str) -> dict:
    """Read versions.toml as it stands on `branch`.

    The two lines are branches of one repository, so the sibling's file is a
    `git show` away rather than another checkout.
    """
    result = subprocess.run(
        ["git", "show", f"origin/{branch}:versions.toml"],
        capture_output=True,
        text=True,
        cwd=get_repo_root(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not read versions.toml from origin/{branch}: "
            f"{result.stderr.strip() or 'unknown error'}"
        )
    return tomllib.loads(result.stdout)


def retired_pins() -> list[str]:
    """Every pin either line has retired, this line's first.

    BOTH lines, always. This deploy PUTs the entire redirect list and the
    workflow fires on either branch, so a run that could see only its own
    retired pins would republish a list missing the other line's and silently
    undo them -- the same failure as deploying from a stale submodule pointer,
    reached from a different direction.

    This line is read from the working tree, not from `origin`, so the file the
    deploy acts on is the one checked out; the sibling has to come from its ref.
    A sibling that cannot be read is fatal rather than skipped: a partial list is
    not a smaller correct answer, it is a wrong one.
    """
    own = tomllib.loads((get_repo_root() / "versions.toml").read_text())
    sibling = SIBLING_BRANCH[_line_of(str(own.get("stable", "v2")))]

    pins = [str(p) for p in own.get("retired", [])]
    for pin in _versions_from_branch(sibling).get("retired", []):
        if str(pin) not in pins:
            pins.append(str(pin))
    return pins


def _line_of(pin: str) -> str:
    """The docs line a pin belongs to: `v2.6.1.0` -> `v2`."""
    return "v" + pin.lstrip("v").split(".", 1)[0]


def retired_pin_items(pins: list[str], existing: set[str]) -> list[dict]:
    """A redirect item per retired pin, skipping any the CSV already covers.

    Every retired pin gets the same rule -- the line root, permanent, matching
    the whole subtree and keeping the path suffix -- so there is no judgement to
    record and nothing to keep in a file. `existing` makes this idempotent
    against a hand-written row that has not been migrated yet; Cloudflare rejects
    a list with two items sharing a source.
    """
    items = []
    for pin in pins:
        source = f"www.union.ai/docs/{pin}"
        if source in existing:
            continue
        items.append(
            {
                "redirect": {
                    "source_url": source,
                    "target_url": f"https://www.union.ai/docs/{_line_of(pin)}",
                    "status_code": 301,
                    "include_subdomains": True,
                    "subpath_matching": True,
                    "preserve_query_string": True,
                    "preserve_path_suffix": True,
                }
            }
        )
    return items


def cf_api_request(
    method: str, path: str, token: str, body: object = None
) -> dict:
    """Make a Cloudflare API request and return the parsed JSON response."""
    url = f"{CF_API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise CloudflareError(e.code, e.read().decode()) from e


class CloudflareError(RuntimeError):
    """A non-2xx response. Carries enough to decide whether to wait or give up."""

    def __init__(self, status: int, body: str):
        super().__init__(f"Cloudflare API error ({status}): {body}")
        self.status = status
        self.body = body

    @property
    def rate_limited(self) -> bool:
        return self.status == 429 or f'"code": {RATE_LIMITED_CODE}' in self.body.replace(
            '"code":', '"code": '
        )


def _rate_limited_result(result: dict) -> bool:
    """Cloudflare may report the limit in a 200 body rather than a status code."""
    if result.get("success"):
        return False
    return any(e.get("code") == RATE_LIMITED_CODE for e in result.get("errors", []))


def cf_api_request_retrying(method: str, path: str, token: str, body: object = None) -> dict:
    """`cf_api_request`, waiting out a rate limit instead of failing on it.

    Only the rate limit is retried. Anything else is a real error and is raised
    immediately -- retrying a malformed list just spends the budget slower.
    """
    for wait in (*RETRY_BACKOFF_SECONDS, None):
        try:
            result = cf_api_request(method, path, token, body=body)
            if not _rate_limited_result(result):
                return result
            reason = "rate limited (in response body)"
        except CloudflareError as e:
            if not e.rate_limited:
                raise
            reason = f"rate limited (HTTP {e.status})"
        if wait is None:
            break
        print(f"  {reason}; retrying in {wait}s...", file=sys.stderr)
        time.sleep(wait)

    raise RuntimeError(
        "Cloudflare rate limit did not clear after "
        f"{len(RETRY_BACKOFF_SECONDS)} retries over "
        f"{sum(RETRY_BACKOFF_SECONDS)}s. The live redirect list is UNCHANGED and "
        "now lags this branch -- re-run this workflow once the limit clears."
    )


def deploy(items: list[dict], account_id: str, list_id: str, token: str) -> None:
    """Replace all items in the Cloudflare Bulk Redirect List."""
    path = f"/accounts/{account_id}/rules/lists/{list_id}/items"

    print(f"Uploading {len(items)} redirect items to Cloudflare...")
    result = cf_api_request_retrying("PUT", path, token, body=items)

    if not result.get("success"):
        errors = result.get("errors", [])
        print(f"API returned failure: {errors}", file=sys.stderr)
        sys.exit(1)

    operation_id = result.get("result", {}).get("operation_id")
    if not operation_id:
        print("Upload accepted (no async operation ID returned).")
        return

    # Poll for completion
    print(f"Async operation started: {operation_id}")
    poll_path = f"/accounts/{account_id}/rules/lists/bulk_operations/{operation_id}"

    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        time.sleep(POLL_INTERVAL_SECONDS)
        status_result = cf_api_request_retrying("GET", poll_path, token)
        status = status_result.get("result", {}).get("status", "unknown")
        print(f"  Poll {attempt}: {status}")

        if status == "completed":
            print("Redirects deployed successfully.")
            return
        elif status == "failed":
            error = status_result.get("result", {}).get("error", "unknown error")
            print(f"Operation failed: {error}", file=sys.stderr)
            sys.exit(1)

    print(
        f"Operation did not complete after {POLL_MAX_ATTEMPTS} attempts.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy redirects.csv to Cloudflare Bulk Redirect List"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse CSV and print stats without making API calls",
    )
    parser.add_argument(
        "--csv",
        default=REDIRECTS_FILE,
        help=f"Path to redirects CSV (default: {REDIRECTS_FILE})",
    )
    args = parser.parse_args()

    # Resolve CSV path relative to unionai-docs-infra/
    csv_path = INFRA_ROOT / args.csv

    if not csv_path.exists():
        print(f"Error: {csv_path} not found", file=sys.stderr)
        return 1

    items = parse_csv(csv_path)
    print(f"Parsed {len(items)} redirect items from {args.csv}")

    # Retired pins are not stored as rows. They are recorded in each line's
    # versions.toml by the cut that retires them, and their redirect is derived
    # here, so the retirement and the redirect cannot drift apart.
    derived = retired_pin_items(retired_pins(), {i["redirect"]["source_url"] for i in items})
    if derived:
        print(f"Derived {len(derived)} redirect item(s) for retired pins:")
        for item in derived:
            print(f"  {item['redirect']['source_url']} -> {item['redirect']['target_url']}")
        items += derived

    if not items:
        print("No redirect items found. Nothing to deploy.")
        return 0

    if args.dry_run:
        print("\nDry run — no API calls made.")
        print(f"  Total items: {len(items)}")
        print(f"  First item: {items[0]['redirect']['source_url']} -> {items[0]['redirect']['target_url']}")
        print(f"  Last item:  {items[-1]['redirect']['source_url']} -> {items[-1]['redirect']['target_url']}")
        return 0

    # Require env vars for actual deployment
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    list_id = os.environ.get("CLOUDFLARE_LIST_ID")

    missing = []
    if not token:
        missing.append("CLOUDFLARE_API_TOKEN")
    if not account_id:
        missing.append("CLOUDFLARE_ACCOUNT_ID")
    if not list_id:
        missing.append("CLOUDFLARE_LIST_ID")

    if missing:
        print(
            f"Error: missing environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        print("Set these or use --dry-run to test CSV parsing.", file=sys.stderr)
        return 1

    try:
        deploy(items, account_id, list_id, token)
    except (CloudflareError, RuntimeError) as e:
        # Fail loudly and say what state the live list is in. A redirect deploy
        # that fails quietly leaves stale redirects, and the symptom is 404s
        # nobody connects to a red workflow from days earlier.
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
