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
from _versions import extract_frontmatter_version, pypi_version_exists

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import get_repo_root

REPO_ROOT = get_repo_root()
CONFIG_FILE = REPO_ROOT / "api-packages.toml"
MANUAL_VERSIONS_FILE = REPO_ROOT / "manual-versions.toml"
# The served-versions intent file (DOC-1245): stable = the tag /docs/v2 serves;
# enumerated = every tag also published as a pinned /docs/v2.x.y.z copy. `--promote`
# writes it; build-and-deploy reads it and materializes any named-but-missing tag.
VERSIONS_FILE = REPO_ROOT / "versions.toml"

# Per-branch docs-version wiring. Defaults target the v2 line (flyte-sdk); a branch
# overrides them in api-packages.toml's [docs_version] section. This is what lets the
# SAME resolver drive both the v2 line (flyte-sdk) and the v1 line (flytekit) — the
# docs version is SDK-triple + z, and each line's SDK major (2 / 1) yields v2.x.y.z /
# v1.x.y.z naturally. The "passenger" is the one independently-versioned sub-part
# (v2: flyteplugins-union, its own 0.x line; v1: the union SDK, its own 0.x line);
# every other flyteplugins-* is lockstep with the SDK. See DOC-1245.
_VERSION_DEFAULTS = {
    "sdk_package": "flyte",
    "sdk_label": "flyte-sdk",
    "passenger_package": "flyteplugins-union",
    "passenger_label": "flyteplugins-union",
    "backend_repo": "flyteorg/flyte",
    "backend_series": "v2.0.",  # the backend release line to track
}


def version_config(config: dict) -> dict:
    """The [docs_version] wiring for this branch, defaulting to the v2 values."""
    dv = config.get("docs_version", {})
    return {**_VERSION_DEFAULTS, **{k: dv[k] for k in _VERSION_DEFAULTS if k in dv}}


def _version_file(config: dict, package: str) -> str | None:
    """API-ref frontmatter path for a package, searching [[sdks]] then [[plugins]]."""
    for sdk in config.get("sdks", []):
        if sdk.get("package") == package:
            return sdk.get("version_file")
    base = config.get("plugins_config", {}).get(
        "output_base", "content/api-reference/integrations"
    )
    for plugin in config.get("plugins", []):
        if plugin.get("package") == package:
            folder = plugin.get("output_folder") or f"{base}/{plugin['name']}"
            return f"{folder}/_index.md"
    return None


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
    """Docs SDK version = committed frontmatter of the SDK API-ref index (flyte / flytekit)."""
    vf = _version_file(config, version_config(config)["sdk_package"])
    return extract_frontmatter_version(REPO_ROOT / vf) if vf else None


def resolve_passenger(config: dict) -> str | None:
    """The one independently-versioned passenger (v2: flyteplugins-union; v1: union SDK)."""
    vf = _version_file(config, version_config(config)["passenger_package"])
    return extract_frontmatter_version(REPO_ROOT / vf) if vf else None


def resolve_flyte_backend(config: dict) -> str | None:
    """Newest backend release on the tracked line (v2.0.x / v1.x), via the GitHub REST API."""
    vc = version_config(config)
    repo, backend_series = vc["backend_repo"], vc["backend_series"]
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.loads(resp.read())
    except Exception as e:
        print(f"  Warning: failed to query {repo} releases: {e}", file=sys.stderr)
        return None

    series = []
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        if tag.startswith(backend_series):
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
    vc = version_config(config)
    sdk = resolve_sdk(config)
    components: dict[str, str | None] = {vc["sdk_label"]: sdk}

    if variant == "union":
        components[vc["passenger_label"]] = resolve_passenger(config)
        components["backend"] = resolve_union_backend()
    else:  # flyte
        components["backend"] = resolve_flyte_backend(config)

    components["examples"] = submodule_sha("unionai-examples")
    components["content"] = _git("rev-parse", "HEAD")
    components["infra"] = submodule_sha("unionai-docs-infra")

    return {
        "variant": variant,
        "flyte_sdk": sdk,
        "components": components,
    }


def build_combined(config: dict, variants: list[str], version: dict) -> dict:
    """The manifest a cut commits: the version decision + every variant's components."""
    return {
        **version,
        "sdk_label": version_config(config)["sdk_label"],
        "variants": {v: build_manifest(v, config)["components"] for v in variants},
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
# --promote: write the version intent into versions.toml
# --------------------------------------------------------------------------- #
def _emit_versions_toml(stable: str, enumerated: list[str]) -> str:
    """Render versions.toml (kept minimal + deterministic so diffs are clean)."""
    def _key(tag: str):
        try:
            return Version(tag.lstrip("v"))
        except Exception:
            return Version("0")
    ordered = sorted(set(enumerated), key=_key)
    lines = [
        "# Docs versions served (DOC-1245). Managed by `manifest.py --promote`;",
        "# build-and-deploy reads it and materializes any named-but-missing tag.",
        f'stable = "{stable}"',
        "enumerated = [",
        *[f'  "{t}",' for t in ordered],
        "]",
        "",
    ]
    return "\n".join(lines)


def promote_versions_toml(decision: dict, path: Path) -> None:
    """Set ``stable`` to the next-cut tag and add it to ``enumerated`` in versions.toml.

    Refuses to promote an SDK version that isn't published on PyPI (defense-in-depth,
    same guard the cut applies) so a hand-edited / dev-build version can't be promoted.
    """
    if decision.get("sdk_on_pypi") == "false":
        sys.exit(
            f"promote: flyte-sdk {decision.get('sdk')} is NOT a published release on "
            f"PyPI -- refusing to promote {decision['tag']}. Fix the API-ref 'version:'."
        )
    if decision.get("sdk_on_pypi") == "unknown":
        print(f"promote: WARNING could not verify {decision.get('sdk')} on PyPI "
              "(network?) -- proceeding.", file=sys.stderr)
    tag = decision["tag"]
    enumerated: list[str] = []
    if path.exists():
        existing = tomllib.loads(path.read_text())
        enumerated = list(existing.get("enumerated", []))
    enumerated.append(tag)
    path.write_text(_emit_versions_toml(stable=tag, enumerated=enumerated))
    print(f"promote: {path.name} stable={tag} (enumerated += {tag})")


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
                       help="Resolve and write the combined manifest.json to --out.")
    group.add_argument("--promote", action="store_true",
                       help="Write the next-cut tag into versions.toml (stable + "
                            "enumerated). The one-merge intent step; refuses a non-PyPI SDK.")
    parser.add_argument("--variant", choices=["union", "flyte", "both"], default="both")
    parser.add_argument("--out", type=Path, help="Output path for --write (manifest.json)")
    parser.add_argument("--format", choices=["pretty", "json", "shell"], default="pretty",
                        help="--check output format. json/shell are machine-readable "
                             "(the version decision, for the cut workflow).")
    args = parser.parse_args()

    config = load_config()
    variants = ["union", "flyte"] if args.variant == "both" else [args.variant]

    manifests = {}
    for variant in variants:
        m = build_manifest(variant, config)
        ver = compute_next_version(m["flyte_sdk"]) if m["flyte_sdk"] else None
        manifests[variant] = (m, ver)

    # The version decision is variant-independent (all variants share the SDK triple).
    decision = next((ver for _, ver in manifests.values() if ver), None)

    # Safety (DOC-1245): a cut must be pinned to a real flyte release. Record whether
    # the committed SDK version is actually published on PyPI, so the cut can refuse
    # to tag a hand-edited or dev-build (e.g. setuptools_scm dirty-tree) version.
    sdk = next((m["flyte_sdk"] for m, _ in manifests.values() if m["flyte_sdk"]), None)
    if decision is not None and sdk is not None:
        exists = pypi_version_exists(version_config(config)["sdk_package"], sdk)
        decision = {
            **decision,
            "sdk": sdk,
            "sdk_on_pypi": "unknown" if exists is None else ("true" if exists else "false"),
        }

    if args.check:
        if args.format == "json":
            print(json.dumps(decision or {}))
            return
        if args.format == "shell":
            for key in ("tag", "docs_version", "cut_kind", "z", "sdk", "sdk_on_pypi"):
                print(f"DOCS_{key.upper()}={(decision or {}).get(key, '')}")
            return
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

    if args.promote:
        if decision is None:
            parser.error("cannot resolve the flyte-sdk version; refusing to promote")
        promote_versions_toml(decision, args.out or VERSIONS_FILE)
        return

    # --write: the combined manifest a cut commits (all variants + the version decision).
    if not args.out:
        parser.error("--write requires an --out path")
    if decision is None:
        parser.error("cannot resolve the flyte-sdk version; refusing to write a manifest")
    combined = build_combined(config, variants, decision)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(combined, indent=2) + "\n")
    print(f"Wrote {args.out}  ({combined['tag']}, {combined['cut_kind']})")


if __name__ == "__main__":
    main()
