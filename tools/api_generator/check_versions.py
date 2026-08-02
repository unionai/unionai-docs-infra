#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "packaging",
#     "tomli; python_version < '3.11'",
# ]
# ///
"""
Check if committed API docs are up-to-date with PyPI releases.

Modes:
  --check   Compare committed versions vs PyPI latest. Exit 0 if current, 1 if outdated.
  --update  Same check, but prompt to regenerate if outdated (interactive only).

Reads api-packages.toml for the list of packages and their version files.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _versions import extract_frontmatter_version, get_pypi_latest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import get_repo_root

REPO_ROOT = get_repo_root()
CONFIG_FILE = REPO_ROOT / "api-packages.toml"


def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def check_all(config: dict) -> list[dict]:
    """Check all packages. Returns list of dicts with package info and status."""
    results = []
    plugins_config = config.get("plugins_config", {})
    output_base = plugins_config.get("output_base", "content/api-reference/integrations")

    # SDKs
    for sdk in config.get("sdks", []):
        if sdk.get("frozen", False):
            continue
        version_file = REPO_ROOT / sdk["version_file"]
        committed = extract_frontmatter_version(version_file)
        latest = get_pypi_latest(sdk["package"])
        output = REPO_ROOT / sdk["output_folder"]
        # classes can be a directory (no-flatten) or a single .md file (flatten)
        # DOC-1335: the generated tree is hoisted -- no `packages/` or `classes/`
        # wrappers. "Content present" = the landing page plus at least one other
        # generated markdown file (a module dir or a class page).
        landing = output / "_index.md"
        others = [f for f in output.rglob("*.md") if f != landing] if output.is_dir() else []
        content_missing = not landing.is_file() or not others
        results.append({
            "type": "sdk",
            "package": sdk["package"],
            "committed": committed,
            "latest": latest,
            "outdated": _is_outdated(committed, latest) or content_missing,
            "version_file": sdk["version_file"],
        })

    # Plugins
    for plugin in config.get("plugins", []):
        if plugin.get("frozen", False):
            continue
        # A plugin may override its output location to be promoted out of the shared
        # plugins output_base (e.g. union-plugin → a top-level api-reference section).
        output_folder = plugin.get("output_folder") or f"{output_base}/{plugin['name']}"
        version_file = REPO_ROOT / output_folder / "_index.md"
        committed = extract_frontmatter_version(version_file)
        latest = get_pypi_latest(plugin["package"])
        results.append({
            "type": "plugin",
            "package": plugin["package"],
            "plugin": plugin["plugin"],
            "name": plugin["name"],
            "title": plugin["title"],
            "install": plugin.get("install"),
            "extras": plugin.get("extras", []),
            "output_folder": output_folder,
            "weight": plugin.get("weight"),
            "variants": plugin.get("variants"),
            "committed": committed,
            "latest": latest,
            "outdated": _is_outdated(committed, latest),
            "version_file": f"{output_folder}/_index.md",
        })

    # CLIs
    for cli in config.get("clis", []):
        if cli.get("frozen", False):
            continue
        if "output_file" in cli:
            output_path = REPO_ROOT / cli["output_file"]
            content_missing = not output_path.is_file()
            committed = None if content_missing else extract_frontmatter_version(output_path)
        elif "output_dir" in cli:
            output_path = REPO_ROOT / cli["output_dir"] / "_index.md"
            content_missing = not (REPO_ROOT / cli["output_dir"]).is_dir()
            committed = None if content_missing else extract_frontmatter_version(output_path)
        else:
            content_missing = False
            committed = None
        package = cli.get("package", cli["name"])
        latest = get_pypi_latest(package) if package else None
        results.append({
            "type": "cli",
            "name": cli["name"],
            "package": package,
            "committed": committed,
            "latest": latest,
            "outdated": content_missing or _is_outdated(committed, latest),
        })

    return results


def _is_outdated(committed: str | None, latest: str | None) -> bool:
    """Return True if committed version is older than latest, or docs don't exist yet."""
    if latest is None:
        return False
    if committed is None:
        # No committed docs yet — outdated if there's a version on PyPI
        return True
    try:
        return Version(committed) < Version(latest)
    except Exception:
        return False


def print_results(results: list[dict]) -> None:
    for r in results:
        status = "OUTDATED" if r["outdated"] else "up-to-date"
        committed = r["committed"] or "not generated"
        latest = r["latest"] or "unknown"
        if r["type"] == "cli":
            print(f"  {r['name']}-cli: committed={committed} latest={latest} [{status}]")
        else:
            print(f"  {r['package']}: committed={committed} latest={latest} [{status}]")


def regenerate(results: list[dict]) -> None:
    """Invoke existing Makefiles to regenerate outdated docs.

    Sets up the shared SDK/CLI venv once, then runs each generator.
    """
    # Set up the shared venv used by SDK and CLI generation.
    print("\nSetting up shared venv...")
    subprocess.run(
        ["make", "-f", "unionai-docs-infra/Makefile.api.sdk", "setup-venv"],
        cwd=REPO_ROOT,
        check=True,
    )

    # Regenerate all SDKs together
    has_outdated_sdk = any(r["outdated"] and r["type"] == "sdk" for r in results)
    if has_outdated_sdk:
        outdated_sdks = [r["package"] for r in results if r["outdated"] and r["type"] == "sdk"]
        print(f"\nRegenerating SDK docs ({', '.join(outdated_sdks)})...")
        subprocess.run(
            ["make", "-f", "unionai-docs-infra/Makefile.api.sdk", "sdks"],
            cwd=REPO_ROOT,
            check=True,
        )

    # Regenerate all CLIs together
    has_outdated_cli = any(r["outdated"] and r["type"] == "cli" for r in results)
    if has_outdated_cli:
        outdated_clis = [r["name"] for r in results if r["outdated"] and r["type"] == "cli"]
        print(f"\nRegenerating CLI docs ({', '.join(outdated_clis)})...")
        subprocess.run(
            ["make", "-f", "unionai-docs-infra/Makefile.api.sdk", "clis"],
            cwd=REPO_ROOT,
            check=True,
        )

    # Regenerate outdated plugins
    for r in results:
        if not r["outdated"]:
            continue
        if r["type"] == "plugin":
            print(f"\nRegenerating plugin docs ({r['package']})...")
            cmd = [
                "make", "-f", "unionai-docs-infra/Makefile.api.plugins",
                f"PLUGIN={r['plugin']}", f"TITLE={r['title']}", f"NAME={r['name']}",
            ]
            # Promoted plugins override their output folder (and optionally nav weight).
            if r.get("output_folder"):
                cmd.append(f"OUTPUT_FOLDER={r['output_folder']}")
            if r.get("weight") is not None:
                cmd.append(f"WEIGHT={r['weight']}")
            # A plugin may restrict which variants it appears in (e.g. union-plugin
            # is Union-only); defaults to "+flyte +union" in the Makefile otherwise.
            if r.get("variants"):
                cmd.append(f"VARIANTS={r['variants']}")
            subprocess.run(cmd, cwd=REPO_ROOT, check=True)

    # Clean up shared venv
    subprocess.run(
        ["make", "-f", "unionai-docs-infra/Makefile.api.sdk", "clean-venv"],
        cwd=REPO_ROOT,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Check API doc versions against PyPI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="Check versions and exit with status code")
    group.add_argument("--update", action="store_true",
                       help="Check versions and prompt to regenerate if outdated")
    args = parser.parse_args()

    config = load_config()
    print("Checking API doc versions against PyPI...")
    results = check_all(config)
    print_results(results)

    outdated = [r for r in results if r["outdated"]]

    if not outdated:
        print("All API docs are up-to-date.")
        return

    if args.check:
        print(f"\n{len(outdated)} item(s) are outdated.")
        print("Run 'make update-api-docs' locally to regenerate.")
        sys.exit(1)

    # --update mode
    print(f"\n{len(outdated)} item(s) need regeneration:")
    for r in outdated:
        committed = r["committed"] or "not generated"
        latest = r["latest"] or "unknown"
        if r["type"] == "cli":
            print(f"  {r['name']}-cli: {committed} -> {latest}")
        else:
            print(f"  {r['package']}: {committed} -> {latest}")
    regenerate(outdated)

    print("\nDone. Review and commit the updated docs.")


if __name__ == "__main__":
    main()
