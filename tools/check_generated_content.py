#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""
Check that all generated content files exist in the repository.

Reads api-packages.toml and verifies expected files for:
  - SDK API docs (packages/ and classes/ under each SDK's output_folder)
  - CLI docs (each CLI's output_file or output_dir)
  - Plugin API docs (under plugins_config.output_base/{name}/)
  - Linkmap JSON files (linkmap/{generator_name}-linkmap.json, linkmap/{plugin_name}-linkmap.json)

Exit codes: 0 = all present, 1 = missing content detected.
"""

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from _repo import get_repo_root

REPO_ROOT = get_repo_root()
CONFIG_FILE = REPO_ROOT / "api-packages.toml"


def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def has_md_files(directory: Path) -> bool:
    """Check if a directory exists and contains .md files beyond its own _index.md."""
    if not directory.is_dir():
        return False
    top_index = directory / "_index.md"
    return any(p.suffix == ".md" and p != top_index for p in directory.rglob("*.md"))


def check_all(config: dict) -> list[str]:
    """Check all categories of generated content. Returns list of error messages."""
    errors = []

    # --- SDK API docs ---
    for sdk in config.get("sdks", []):
        if sdk.get("frozen", False):
            continue
        output_folder = sdk["output_folder"]
        # DOC-1335: the generated tree is hoisted -- module dirs (or flatten-mode
        # module files) sit directly under the section root; there are no
        # `packages/` or `classes/` wrappers. "Content present" = the landing
        # plus at least one more generated markdown file.
        sdk_root = REPO_ROOT / output_folder
        if not (sdk_root / "_index.md").is_file():
            errors.append(f"SDK API docs: missing {output_folder}/_index.md")
        elif not has_md_files(sdk_root):
            errors.append(f"SDK API docs: no generated .md files under {output_folder}/")

        # Linkmap JSON
        gen_name = sdk["generator_name"]
        linkmap_file = REPO_ROOT / "linkmap" / f"{gen_name}-linkmap.json"
        if not linkmap_file.is_file():
            errors.append(f"Linkmap: missing linkmap/{gen_name}-linkmap.json")

    # --- CLI docs ---
    for cli in config.get("clis", []):
        if cli.get("frozen", False):
            continue
        if "output_file" in cli:
            cli_path = REPO_ROOT / cli["output_file"]
            if not cli_path.is_file():
                errors.append(f"CLI docs: missing {cli['output_file']}")
        elif "output_dir" in cli:
            cli_path = REPO_ROOT / cli["output_dir"]
            if not cli_path.is_dir():
                errors.append(f"CLI docs: directory missing: {cli['output_dir']}/")
            elif not has_md_files(cli_path):
                errors.append(f"CLI docs: no .md files in {cli['output_dir']}/")

    # --- Plugin API docs ---
    plugins_config = config.get("plugins_config", {})
    output_base = plugins_config.get("output_base", "content/api-reference/integrations")
    check_plugin_linkmaps = plugins_config.get("check_linkmaps", True)

    plugins = config.get("plugins", [])
    for plugin in plugins:
        if plugin.get("frozen", False):
            continue
        name = plugin["name"]
        # A promoted plugin may override its location via `output_folder` (a
        # repo-relative path), the same field honored by Makefile.api.plugins on
        # regen. Without this the validator assumes the default `output_base/name`
        # layout and reports a spurious "directory missing".
        output_folder = plugin.get("output_folder")
        if output_folder:
            plugin_dir = REPO_ROOT / output_folder
            plugin_rel = output_folder
        else:
            plugin_dir = REPO_ROOT / output_base / name
            plugin_rel = f"{output_base}/{name}"
        if not plugin_dir.is_dir():
            errors.append(f"Plugin '{name}': directory missing: {plugin_rel}/")
        elif not has_md_files(plugin_dir):
            # DOC-1335: a single-package plugin with no classes generates ONLY its
            # landing page (the module body is merged into _index.md), so an
            # _index-only section is valid when that landing is substantive.
            plugin_index = plugin_dir / "_index.md"
            if not (plugin_index.is_file() and plugin_index.stat().st_size > 200):
                errors.append(f"Plugin '{name}': no generated content in {plugin_rel}/")

        if check_plugin_linkmaps:
            # Linkmap JSON
            linkmap_file = REPO_ROOT / "linkmap" / f"{name}-linkmap.json"
            if not linkmap_file.is_file():
                errors.append(f"Linkmap: missing linkmap/{name}-linkmap.json")

    return errors


def main():
    config = load_config()
    errors = check_all(config)

    if errors:
        print(f"Found {len(errors)} missing generated content item(s):\n")
        for error in errors:
            print(f"  ::error::{error}")
        print(f"\nRun 'make dist' to regenerate missing content.")
        sys.exit(1)
    else:
        print("All generated content is present.")
        sys.exit(0)


if __name__ == "__main__":
    main()
