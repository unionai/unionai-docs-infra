#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "packaging",
#     "tomli; python_version < '3.11'",
# ]
# ///
"""
Find released plugins that api-packages.toml does not register.

The regen pipeline only documents what the registry lists, so a plugin added to
the SDK repo gets no API reference until someone adds a [[plugins]] entry by
hand -- and nothing used to notice when nobody did (dbt, hitl, lance, llamacpp,
otel, pandera, slurm and trackio all shipped undocumented).

Opt-in: a branch whose api-packages.toml has no [plugin_discovery] table (v1)
skips the check. With one:

    [plugin_discovery]
    repo = "flyteorg/flyte-sdk"   # GitHub repo holding the plugins
    ref = "main"
    path = "plugins"              # every pyproject.toml under here is a plugin
    lockstep = "flyte"            # see "released" below

    [[plugin_discovery.skip]]     # deliberately undocumented
    package = "flyteplugins-echo"
    reason = "test fixture, not a user-facing plugin"

Each discovered package is classified against PyPI:

  registered    listed in [[plugins]] (frozen included) or [[sdks]]; ignored
  skipped       listed in [[plugin_discovery.skip]]; ignored
  UNREGISTERED  released and not registered -- fails the check
  claimed       on PyPI, but only as a name claim; reported, does not fail
  unreleased    no PyPI project yet; reported, does not fail

"Released" means a final, non-yanked release in the lockstep package's major
line. In-repo plugins version in lockstep with the SDK, so a real release of a
2.x plugin is 2.x. Names are claimed ahead of release with a `0.0.0a0`
pre-release whose wheel holds the real code, so size and metadata cannot tell a
claim from a release; only the version can.

Exit status: 0 when nothing is unregistered, 1 when something is, 2 when
discovery could not complete. An incomplete scan is reported as a failure, not
as a clean result.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from packaging.version import InvalidVersion, Version

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _versions import get_pypi_latest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import get_repo_root

UNREGISTERED = "UNREGISTERED"
CLAIMED = "claimed"
UNRELEASED = "unreleased"


class DiscoveryError(Exception):
    """The scan could not see the whole plugin tree or the PyPI state it needs."""


def classify(releases: dict[str, list[dict]] | None, lockstep_major: int) -> str:
    """Classify one package from its PyPI `releases` map.

    `releases` is the JSON API's version -> files map, or None when PyPI has no
    such project.
    """
    if releases is None:
        return UNRELEASED
    for ver_str, files in releases.items():
        if not files or all(f.get("yanked", False) for f in files):
            continue
        try:
            v = Version(ver_str)
        except InvalidVersion:
            continue
        if v.is_prerelease or v.is_devrelease:
            continue
        if v.major == lockstep_major:
            return UNREGISTERED
    return CLAIMED


def registered_packages(config: dict) -> set[str]:
    """Every package the registry already accounts for, frozen entries included."""
    return {p["package"] for p in config.get("plugins", [])} | {
        s["package"] for s in config.get("sdks", [])
    }


def _get_json(url: str, token: str | None = None) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def discover_pyprojects(repo: str, ref: str, path: str) -> list[str]:
    """Paths of every pyproject.toml under `path` in `repo@ref`, recursively.

    Raises DiscoveryError if GitHub can't be reached or truncates the tree: a
    partial listing would silently miss plugins.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    try:
        tree = _get_json(url, token)
    except Exception as e:
        raise DiscoveryError(f"could not list {repo}@{ref}: {e}") from e
    if tree.get("truncated"):
        raise DiscoveryError(f"GitHub truncated the tree listing for {repo}@{ref}")
    prefix = path.rstrip("/") + "/"
    return sorted(
        e["path"]
        for e in tree.get("tree", [])
        if e.get("type") == "blob"
        and e["path"].startswith(prefix)
        and e["path"].endswith("/pyproject.toml")
    )


def read_package_name(repo: str, ref: str, pyproject_path: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{pyproject_path}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = tomllib.loads(resp.read().decode())
    except Exception as e:
        raise DiscoveryError(f"could not read {pyproject_path}: {e}") from e
    return data.get("project", {}).get("name")


def pypi_releases(package: str) -> dict[str, list[dict]] | None:
    """The package's PyPI releases map, or None if PyPI has no such project."""
    try:
        return _get_json(f"https://pypi.org/pypi/{package}/json").get("releases", {})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise DiscoveryError(f"PyPI lookup failed for {package}: {e}") from e
    except Exception as e:
        raise DiscoveryError(f"PyPI lookup failed for {package}: {e}") from e


def check(config: dict) -> list[dict] | None:
    """Classify every discovered plugin the registry does not list.

    Returns None when the registry has no [plugin_discovery] table.
    """
    disc = config.get("plugin_discovery")
    if not disc:
        return None
    repo, ref, path = disc["repo"], disc.get("ref", "main"), disc.get("path", "plugins")

    lockstep = get_pypi_latest(disc["lockstep"])
    if lockstep is None:
        raise DiscoveryError(f"could not resolve the latest {disc['lockstep']} release on PyPI")
    lockstep_major = Version(lockstep).major

    pyprojects = discover_pyprojects(repo, ref, path)
    if not pyprojects:
        raise DiscoveryError(f"no pyproject.toml found under {repo}@{ref}/{path}")

    with ThreadPoolExecutor(max_workers=16) as pool:
        names = list(pool.map(lambda p: read_package_name(repo, ref, p), pyprojects))

    known = registered_packages(config)
    skipped = {s["package"] for s in disc.get("skip", [])}
    candidates = sorted(
        {(n, p) for n, p in zip(names, pyprojects) if n and n not in known and n not in skipped}
    )

    with ThreadPoolExecutor(max_workers=16) as pool:
        releases = list(pool.map(lambda c: pypi_releases(c[0]), candidates))

    results = []
    for (name, pyproject), rel in zip(candidates, releases):
        status = classify(rel, lockstep_major)
        latest = None
        if rel:
            versions = []
            for v in rel:
                try:
                    versions.append(Version(v))
                except InvalidVersion:
                    pass
            latest = str(max(versions)) if versions else None
        results.append({
            "package": name,
            "source": pyproject.rsplit("/", 1)[0],
            "status": status,
            "latest": latest,
        })
    return results


def print_results(results: list[dict], repo: str, ref: str) -> None:
    if not results:
        print(f"  Every released plugin in {repo}@{ref} is registered.")
        return
    for r in results:
        latest = f"latest={r['latest']} " if r["latest"] else ""
        print(f"  {r['package']}: {latest}[{r['status']}]  ({r['source']})")


def main():
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip()).parse_args()

    with open(get_repo_root() / "api-packages.toml", "rb") as f:
        config = tomllib.load(f)
    disc = config.get("plugin_discovery")
    if not disc:
        print("No [plugin_discovery] in api-packages.toml; skipping the plugin registry check.")
        return

    print(f"Checking {disc['repo']}@{disc.get('ref', 'main')} plugins against api-packages.toml...")
    try:
        results = check(config)
    except DiscoveryError as e:
        print(f"  [DISCOVERY FAILED] {e}")
        print("\nThe scan is incomplete, so it cannot say whether every plugin is registered.")
        sys.exit(2)

    print_results(results, disc["repo"], disc.get("ref", "main"))
    unregistered = [r for r in results if r["status"] == UNREGISTERED]
    if unregistered:
        print(
            f"\n{len(unregistered)} released plugin(s) have no API reference. Add a "
            "[[plugins]] entry to api-packages.toml for each (or a "
            "[[plugin_discovery.skip]] entry with a reason), then run "
            "'make update-api-docs'."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
