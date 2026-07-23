#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "packaging",
#     "tomli; python_version < '3.11'",
# ]
# ///
"""
Docs-version manifest resolver + cut-action version arithmetic (DOC-1245).

The v2 docs get a modified semver ``v2.x.y.z`` pinned to ``flyte-sdk``. There
are exactly two ways a *cut* creates a version (prds ``docs_versioning`` §9.4):

  1. a ``flyte-sdk`` release        -> new ``x.y.z.0``   (automatic)
  2. a maintainer **manual cut**    -> ``x.y.z.(z+1)``    (human)

Everything else (the backend, ``flyteplugins-union``, examples, infra, docs
content) is a *passenger*: never a trigger, but its current value is collected
into the manifest **at cut time**. This tool is that collection step plus the
``z`` arithmetic.

Sub-parts and where each value is read (per prds §8 SSOT table):

  flyte-sdk (+ in-repo plugins, lockstep)  committed frontmatter version
  flyteplugins-union  (union only)         committed frontmatter version (own 0.x)
  backend  (flyte variant)                 newest flyteorg/flyte v2.0.x release
  backend  (union variant)                 manual-versions.toml (DOC-1276)
  unionai-examples                         submodule gitlink SHA
  unionai-docs content                     the cut commit (HEAD)
  unionai-docs-infra                       submodule gitlink SHA (recorded, not shown)

Modes:
  --check   Resolve the manifest(s) and print the version the next cut would
            produce. Read-only (no writes, no tags). Default variant: both.
  --write   Resolve and write manifest.json for a variant to --out.

Reads api-packages.toml (the package registry, in unionai-docs) for the
flyte-sdk and flyteplugins-union page locations, mirroring check_versions.py.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _versions import extract_frontmatter_version

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import get_repo_root

REPO_ROOT = get_repo_root()
CONFIG_FILE = REPO_ROOT / "api-packages.toml"
MANUAL_VERSIONS_FILE = REPO_ROOT / "manual-versions.toml"

# Package that versions independently of the flyte-sdk monorepo (its own 0.x line,
# unionai/flyteplugins-union). Every other flyteplugins-* is lockstep with flyte.
UNION_PLUGIN_PACKAGE = "flyteplugins-union"
SDK_PACKAGE = "flyte"
FLYTE_BACKEND_REPO = "flyteorg/flyte"
FLYTE_BACKEND_SERIES = "v2.0."  # the v2 backend line


# --------------------------------------------------------------------------- #
# config + git helpers
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _git(*args: str) -> str | None:
    """Run a git command at REPO_ROOT, returning stripped stdout or None on error."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  Warning: git {' '.join(args)} failed: {e}", file=sys.stderr)
        return None


def submodule_sha(path: str) -> str | None:
    """Read a submodule's pinned commit (the gitlink recorded in HEAD).

    Works without initializing the submodule -- the SHA lives in the
    superproject tree, which is exactly what makes the tag reproducible.
    """
    out = _git("ls-tree", "HEAD", path)
    # Format: "160000 commit <sha>\t<path>"
    if out and "commit" in out:
        return out.split()[2]
    return None


# --------------------------------------------------------------------------- #
# per-sub-part resolvers
# --------------------------------------------------------------------------- #
def resolve_sdk(config: dict) -> str | None:
    """flyte-sdk version = committed frontmatter of the SDK API-ref index."""
    for sdk in config.get("sdks", []):
        if sdk.get("package") == SDK_PACKAGE:
            return extract_frontmatter_version(REPO_ROOT / sdk["version_file"])
    return None


def resolve_union_plugin(config: dict) -> str | None:
    """flyteplugins-union version = committed frontmatter of its API-ref index."""
    base = config.get("plugins_config", {}).get(
        "output_base", "content/api-reference/integrations"
    )
    for plugin in config.get("plugins", []):
        if plugin.get("package") == UNION_PLUGIN_PACKAGE:
            folder = plugin.get("output_folder") or f"{base}/{plugin['name']}"
            return extract_frontmatter_version(REPO_ROOT / folder / "_index.md")
    return None


def resolve_flyte_backend() -> str | None:
    """Newest flyteorg/flyte v2.0.x release, via the GitHub REST API."""
    url = f"https://api.github.com/repos/{FLYTE_BACKEND_REPO}/releases?per_page=100"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.loads(resp.read())
    except Exception as e:
        print(f"  Warning: failed to query {FLYTE_BACKEND_REPO} releases: {e}", file=sys.stderr)
        return None

    series = []
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        if tag.startswith(FLYTE_BACKEND_SERIES):
            try:
                series.append((Version(tag.lstrip("v")), tag))
            except Exception:
                continue
    if not series:
        return None
    return max(series, key=lambda t: t[0])[1]  # the tag_name, e.g. "v2.0.28"


def resolve_union_backend() -> str | None:
    """Union backend version -- hand-maintained (no reliable SSOT yet, DOC-1276)."""
    if not MANUAL_VERSIONS_FILE.exists():
        print(
            f"  Warning: {MANUAL_VERSIONS_FILE.name} not found; union backend unknown "
            "(see DOC-1276)",
            file=sys.stderr,
        )
        return None
    with open(MANUAL_VERSIONS_FILE, "rb") as f:
        data = tomllib.load(f)
    return data.get("backend", {}).get("union")


# --------------------------------------------------------------------------- #
# manifest assembly
# --------------------------------------------------------------------------- #
def build_manifest(variant: str, config: dict) -> dict:
    """Collect every sub-part's current value for one variant (union|flyte)."""
    sdk = resolve_sdk(config)
    components: dict[str, str | None] = {"flyte-sdk": sdk}

    if variant == "union":
        components["flyteplugins-union"] = resolve_union_plugin(config)
        components["backend"] = resolve_union_backend()
    else:  # flyte
        components["backend"] = resolve_flyte_backend()

    components["examples"] = submodule_sha("unionai-examples")
    components["content"] = _git("rev-parse", "HEAD")
    components["infra"] = submodule_sha("unionai-docs-infra")

    return {
        "variant": variant,
        "flyte_sdk": sdk,
        "components": components,
    }


# --------------------------------------------------------------------------- #
# cut-action version arithmetic
# --------------------------------------------------------------------------- #
def existing_z(sdk_version: str) -> list[int]:
    """The z values of existing docs tags for this flyte-sdk triple."""
    prefix = f"v{sdk_version}."
    out = _git("tag", "--list", f"{prefix}*") or ""
    zs = []
    for tag in out.splitlines():
        suffix = tag[len(prefix):]
        if suffix.isdigit():
            zs.append(int(suffix))
    return sorted(zs)


def compute_next_version(sdk_version: str) -> dict:
    """Compute the version the next cut would produce for this SDK triple.

    z = 0 for the first cut against a flyte-sdk version (an SDK-release cut);
    z = max(existing z) + 1 for a manual cut against the same SDK version.
    """
    zs = existing_z(sdk_version)
    if not zs:
        z, kind = 0, "sdk-release"
    else:
        z, kind = zs[-1] + 1, "manual"
    return {
        "docs_version": f"{sdk_version}.{z}",
        "tag": f"v{sdk_version}.{z}",
        "z": z,
        "cut_kind": kind,
        "existing_z": zs,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_manifest(m: dict, ver: dict | None) -> None:
    print(f"\n[{m['variant']}] manifest")
    for name, value in m["components"].items():
        shown = value if value is not None else "UNKNOWN"
        print(f"    {name:<20} {shown}")
    if ver:
        print(f"    -> next cut: {ver['tag']}  ({ver['cut_kind']}; z={ver['z']}, "
              f"existing z={ver['existing_z'] or 'none'})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="Resolve manifest(s) + report the next cut version. Read-only.")
    group.add_argument("--write", action="store_true",
                       help="Resolve and write manifest.json for --variant to --out.")
    parser.add_argument("--variant", choices=["union", "flyte", "both"], default="both")
    parser.add_argument("--out", type=Path, help="Output path for --write (manifest.json)")
    args = parser.parse_args()

    config = load_config()
    variants = ["union", "flyte"] if args.variant == "both" else [args.variant]

    manifests = {}
    for variant in variants:
        m = build_manifest(variant, config)
        ver = compute_next_version(m["flyte_sdk"]) if m["flyte_sdk"] else None
        manifests[variant] = (m, ver)

    if args.check:
        print("Resolving docs-version manifest (read-only)...")
        for variant in variants:
            m, ver = manifests[variant]
            _print_manifest(m, ver)
        missing = [
            f"{v}:{name}"
            for v in variants
            for name, val in manifests[v][0]["components"].items()
            if val is None
        ]
        if missing:
            print(f"\n{len(missing)} unresolved sub-part(s): {', '.join(missing)}", file=sys.stderr)
        return

    # --write
    if args.variant == "both" or not args.out:
        parser.error("--write requires a single --variant and an --out path")
    m, ver = manifests[args.variant]
    out = {**m, **(ver or {})}
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
