#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "packaging",
#     "tomlkit",
#     "tomli; python_version < '3.11'",
# ]
# ///
"""
Find released plugins that api-packages.toml does not register, and register them.

The regen pipeline only documents what the registry lists, so a plugin added to
the SDK repo gets no API reference until it has a [[plugins]] entry -- and
nothing used to notice when nobody added one (dbt, hitl, lance, llamacpp, otel,
pandera, slurm and trackio all shipped undocumented). With `--register`, the
entry is written here, so the regen that follows documents the plugin.

Opt-in: a branch whose api-packages.toml has no [plugin_discovery] table (v1)
skips the check. With one:

    [plugin_discovery]
    repo = "flyteorg/flyte-sdk"   # GitHub repo holding the plugins
    ref = "main"
    path = "plugins"              # every pyproject.toml under here is a plugin
    lockstep = "flyte"            # see "released" below
    owners = ["flyte-bot", "unionai-oss"]   # PyPI accounts allowed to own a plugin

    [[plugin_discovery.skip]]     # deliberately undocumented
    package = "flyteplugins-echo"
    reason = "test fixture, not a user-facing plugin"

Each discovered package is classified against PyPI:

  registered    listed in [[plugins]] (frozen included) or [[sdks]]; ignored
  skipped       listed in [[plugin_discovery.skip]]; ignored
  UNREGISTERED  released and not registered -- fails the check; `--register` adds it
  UNVERIFIED    released, but a PyPI owner is outside `owners` -- fails, never registered
  claimed       on PyPI, but only as a name claim; reported, does not fail
  unreleased    no PyPI project yet; reported, does not fail

"Released" means a final, non-yanked release in the lockstep package's major
line. In-repo plugins version in lockstep with the SDK, so a real release of a
2.x plugin is 2.x. Names are claimed ahead of release with a `0.0.0a0`
pre-release whose wheel holds the real code, so size and metadata cannot tell a
claim from a release; only the version can.

"Ours" rests on PyPI's owner accounts (the JSON API's `ownership`), since our
packages carry no publish attestations. Every owner must be in `owners`; one
outside account is enough to withhold registration until a person looks. The
gate only applies when `owners` is configured, and `--register` refuses to run
without it.

`--register` appends a [[plugins]] entry for each UNREGISTERED package. The
mechanical fields come from the source tree (`plugin` is the shallowest
`flyteplugins/.../__init__.py` under the plugin dir -- not the package name
with dashes swapped, see typesafe-ai) and the package name (`name`). `title`
is a guess and the entry lands at the default `integrations/<name>`; the entry
carries a comment asking the reviewer to check title, name and output_folder,
because moving a published page later means a redirect.

Exit status: 0 when nothing is unregistered or unverified (after registering,
with `--register`), 1 when something is, 2 when discovery could not complete.
An incomplete scan is reported as a failure, not as a clean result.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tomlkit
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
UNVERIFIED = "UNVERIFIED"
CLAIMED = "claimed"
UNRELEASED = "unreleased"

PACKAGE_PREFIX = "flyteplugins-"
NAMESPACE = "flyteplugins"


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


def owner_names(ownership: dict | None) -> set[str]:
    """Every account that owns the project: role holders and the organization."""
    if not ownership:
        return set()
    names = {r.get("user") for r in ownership.get("roles", []) if r.get("user")}
    org = ownership.get("organization")
    if org:
        names.add(org.get("name") if isinstance(org, dict) else str(org))
    return names


def owner_verified(ownership: dict | None, allowed) -> bool:
    """True when the project has owners and every one of them is allowed.

    Nothing to verify against is a fail, not a pass: an empty or missing
    `ownership` would otherwise let anything through.
    """
    names = owner_names(ownership)
    return bool(names) and names <= set(allowed)


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


def list_tree(repo: str, ref: str) -> list[str]:
    """Every file path in `repo@ref`.

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
    return sorted(e["path"] for e in tree.get("tree", []) if e.get("type") == "blob")


def pyprojects_in(tree: list[str], path: str) -> list[str]:
    """Paths of every pyproject.toml under `path`, recursively."""
    prefix = path.rstrip("/") + "/"
    return sorted(p for p in tree if p.startswith(prefix) and p.endswith("/pyproject.toml"))


def discover_pyprojects(repo: str, ref: str, path: str) -> list[str]:
    return pyprojects_in(list_tree(repo, ref), path)


def import_path(tree: list[str], source: str) -> str | None:
    """The plugin's import path, read from its source tree.

    The shallowest `flyteplugins/<...>/__init__.py` under the plugin dir is
    the plugin module; deeper ones are its subpackages. `flyteplugins` itself
    is the namespace, never the plugin, even where a stray
    `flyteplugins/__init__.py` ships (omlx, vllm). None when there is no such
    file, which is a plugin the generator could not import anyway.
    """
    prefix = source.rstrip("/") + "/"
    marker = f"/{NAMESPACE}/"
    best: list[str] | None = None
    for p in tree:
        if not (p.startswith(prefix) and p.endswith("/__init__.py")):
            continue
        i = p.find(marker)
        if i < 0:
            continue
        parts = p[i + 1 : -len("/__init__.py")].split("/")
        if len(parts) < 2:
            continue
        if best is None or len(parts) < len(best):
            best = parts
    return ".".join(best) if best else None


def read_package_name(repo: str, ref: str, pyproject_path: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{pyproject_path}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = tomllib.loads(resp.read().decode())
    except Exception as e:
        raise DiscoveryError(f"could not read {pyproject_path}: {e}") from e
    return data.get("project", {}).get("name")


def pypi_project(package: str) -> dict | None:
    """The package's PyPI JSON (releases, ownership), or None if PyPI has no such project."""
    try:
        return _get_json(f"https://pypi.org/pypi/{package}/json")
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

    tree = list_tree(repo, ref)
    pyprojects = pyprojects_in(tree, path)
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
        projects = list(pool.map(lambda c: pypi_project(c[0]), candidates))

    owners = disc.get("owners")
    results = []
    for (name, pyproject), proj in zip(candidates, projects):
        rel = proj.get("releases", {}) if proj is not None else None
        ownership = proj.get("ownership") if proj is not None else None
        status = classify(rel, lockstep_major)
        if status == UNREGISTERED and owners is not None and not owner_verified(ownership, owners):
            status = UNVERIFIED
        latest = None
        if rel:
            versions = []
            for v in rel:
                try:
                    versions.append(Version(v))
                except InvalidVersion:
                    pass
            latest = str(max(versions)) if versions else None
        source = pyproject.rsplit("/", 1)[0]
        results.append({
            "package": name,
            "source": source,
            "status": status,
            "latest": latest,
            "owners": sorted(owner_names(ownership)),
            "plugin": import_path(tree, source),
        })
    return results


def new_entry(package: str, plugin: str) -> dict:
    """A [[plugins]] entry with the mechanical fields filled in.

    `title` is a guess from the package name and the entry takes the default
    output folder; both are for the reviewer to correct before merging.
    """
    name = package.removeprefix(PACKAGE_PREFIX)
    return {
        "package": package,
        "plugin": plugin,
        "title": name.replace("-", " ").title(),
        "name": name,
    }


def registrable(results: list[dict], config: dict) -> list[dict]:
    """Entries for every result that passed both gates.

    Refuses outright, rather than registering a subset, when the owner
    allowlist is missing (nothing was verified) or a release has no import
    path (the generator would have nothing to import).
    """
    if not config.get("plugin_discovery", {}).get("owners"):
        raise DiscoveryError(
            "registering needs [plugin_discovery].owners, the PyPI accounts allowed "
            "to own a plugin; without it nothing is verified as ours"
        )
    entries = []
    for r in results:
        if r["status"] != UNREGISTERED:
            continue
        if not r.get("plugin"):
            raise DiscoveryError(
                f"{r['package']}: no {NAMESPACE}/.../__init__.py under {r.get('source')}, "
                "so its import path is unknown"
            )
        entries.append(new_entry(r["package"], r["plugin"]))
    return entries


def register_entries(path: Path, entries: list[dict], today: datetime.date | None = None) -> list[dict]:
    """Append `entries` to the [[plugins]] array in `path`; returns those written.

    tomlkit keeps the file's comments and layout. An entry whose package is
    already present is skipped, so a re-run is a no-op.
    """
    doc = tomlkit.parse(path.read_text())
    plugins = doc.get("plugins")
    if plugins is None:
        plugins = tomlkit.aot()
        doc["plugins"] = plugins
    present = {p["package"] for p in plugins}
    stamp = (today or datetime.date.today()).isoformat()
    written = []
    for entry in entries:
        if entry["package"] in present:
            continue
        table = tomlkit.table()
        table.add(tomlkit.comment(
            f"auto-registered {stamp}: check title, name and output_folder before merging, then drop this comment"
        ))
        for key, value in entry.items():
            table.add(key, value)
        table.add(tomlkit.nl())  # a blank line after, as between the hand-written entries
        plugins.append(table)
        present.add(entry["package"])
        written.append(entry)
    if written:
        path.write_text(tomlkit.dumps(doc))
    return written


def print_results(results: list[dict], repo: str, ref: str) -> None:
    if not results:
        print(f"  Every released plugin in {repo}@{ref} is registered.")
        return
    for r in results:
        latest = f"latest={r['latest']} " if r["latest"] else ""
        owners = f"  owners={','.join(r['owners'])}" if r["status"] == UNVERIFIED else ""
        print(f"  {r['package']}: {latest}[{r['status']}]  ({r['source']}){owners}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument(
        "--register",
        action="store_true",
        help="append a [[plugins]] entry to api-packages.toml for each UNREGISTERED plugin",
    )
    args = parser.parse_args()

    registry = get_repo_root() / "api-packages.toml"
    with open(registry, "rb") as f:
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

    if args.register:
        try:
            written = register_entries(registry, registrable(results, config))
        except DiscoveryError as e:
            print(f"  [REGISTRATION FAILED] {e}")
            sys.exit(2)
        for e in written:
            print(
                f"  {e['package']}: [REGISTERED] plugin={e['plugin']} name={e['name']} "
                f"title={e['title']!r}  -- check title, name and output_folder"
            )
        if written:
            print(f"\nRegistered {len(written)} plugin(s) in {registry.name}; the next regen documents them.")

    unverified = [r for r in results if r["status"] == UNVERIFIED]
    if unverified:
        print(
            f"\n{len(unverified)} released plugin(s) have a PyPI owner outside "
            "[plugin_discovery].owners. Not registered: confirm the package is ours, then "
            "either add the owner to the allowlist or add a [[plugin_discovery.skip]] entry."
        )
        sys.exit(1)

    unregistered = [r for r in results if r["status"] == UNREGISTERED]
    if unregistered and not args.register:
        print(
            f"\n{len(unregistered)} released plugin(s) have no API reference. Run "
            "'make register-plugins' (or add a [[plugins]] entry to api-packages.toml, "
            "or a [[plugin_discovery.skip]] entry with a reason), then "
            "'make update-api-docs'."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
