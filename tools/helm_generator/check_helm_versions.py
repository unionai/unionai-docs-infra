#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""
Check if committed Helm chart docs are up-to-date with the helm-charts repo.

Modes:
  --check   Compare committed chart versions vs latest. Exit 0 if current, 1 if outdated.
  --update  Same check, but auto-regenerate if outdated (requires helm-docs binary).

Reads chart_version: from Hugo YAML frontmatter in each generated content file.
Queries GitHub API for the latest Chart.yaml in unionai/helm-charts.
"""

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import get_repo_root

REPO_ROOT = get_repo_root()
INFRA_ROOT = Path(__file__).resolve().parent.parent.parent

# Charts to check: (chart_name, content_file_relative_to_repo_root)
CHARTS = [
    ("dataplane", "content/deployment/helm-chart-reference/dataplane.md"),
    ("knative-operator", "content/deployment/helm-chart-reference/knative-operator.md"),
]

HELM_CHARTS_REPO = "unionai/helm-charts"


def extract_frontmatter_version(filepath: Path) -> str | None:
    """Extract chart_version: field from Hugo YAML frontmatter."""
    if not filepath.exists():
        return None
    text = filepath.read_text()
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("chart_version:"):
            return line.split(":", 1)[1].strip()
    return None


def get_latest_chart_version(chart_name: str) -> str | None:
    """Query GitHub API for the latest chart version from Chart.yaml."""
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{HELM_CHARTS_REPO}/contents/charts/{chart_name}/Chart.yaml",
                "--jq", ".content",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"  Warning: GitHub API query failed for {chart_name}: {result.stderr.strip()}", file=sys.stderr)
            return None

        content = base64.b64decode(result.stdout.strip()).decode()
        for line in content.splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("'\"")
        return None
    except FileNotFoundError:
        print("  Warning: 'gh' CLI not found, skipping GitHub API check", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"  Warning: GitHub API query timed out for {chart_name}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Warning: failed to query chart version for {chart_name}: {e}", file=sys.stderr)
        return None


def check_all() -> list[dict]:
    """Check all charts. Returns list of dicts with chart info and status."""
    results = []
    for chart_name, content_file in CHARTS:
        filepath = REPO_ROOT / content_file
        committed = extract_frontmatter_version(filepath)
        latest = get_latest_chart_version(chart_name)
        outdated = False
        if committed is None:
            outdated = filepath.exists()  # file exists but no chart_version
        elif latest is not None and committed != latest:
            outdated = True
        results.append({
            "chart": chart_name,
            "committed": committed,
            "latest": latest,
            "outdated": outdated,
            "content_file": content_file,
        })
    return results


def print_results(results: list[dict]) -> None:
    for r in results:
        committed = r["committed"] or "not set"
        latest = r["latest"] or "unknown"
        status = "OUTDATED" if r["outdated"] else "up-to-date"
        print(f"  {r['chart']}: committed={committed} latest={latest} [{status}]")


def regenerate() -> None:
    """Invoke the generation script to regenerate helm docs."""
    script = INFRA_ROOT / "tools" / "helm_generator" / "generate_helm_docs.sh"
    print("\nRegenerating helm chart docs...")
    subprocess.run([str(script)], cwd=REPO_ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description="Check Helm chart doc versions")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="Check versions and exit with status code")
    group.add_argument("--update", action="store_true",
                       help="Check versions and regenerate if outdated")
    args = parser.parse_args()

    print("Checking Helm chart doc versions...")
    results = check_all()
    print_results(results)

    outdated = [r for r in results if r["outdated"]]

    if not outdated:
        print("All Helm chart docs are up-to-date.")
        return

    if args.check:
        print(f"\n{len(outdated)} chart(s) have newer versions in {HELM_CHARTS_REPO}.")
        print("Run 'make update-helm-docs' or 'make dist' locally to regenerate.")
        sys.exit(1)

    # --update mode
    print(f"\n{len(outdated)} chart(s) need regeneration:")
    for r in outdated:
        print(f"  {r['chart']}: {r['committed'] or 'not set'} -> {r['latest']}")
    regenerate()
    print("\nDone. Review and commit the updated docs.")


if __name__ == "__main__":
    main()
