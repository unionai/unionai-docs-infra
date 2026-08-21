#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Iterate [[clis]] from api-packages.toml and generate CLI docs for each.

Supports two types of CLIs:
  - Python CLIs (default): uses gen_command from config, run in the shared venv
  - Go CLIs (type = "go"): uses scripts/gen-cli-docs with the binary name

Expects a shared .venv to already exist (created by api_venv_setup.py).
When SKIP_VENV_SETUP=true, uses the current environment instead.
"""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from _repo import get_repo_root, INFRA_ROOT

REPO_ROOT = get_repo_root()
CONFIG_FILE = REPO_ROOT / "api-packages.toml"
VENV_DIR = REPO_ROOT / ".venv"


def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


# Smoke-test floors for generated CLI docs (DOC-1481 step 3).
#
# The walker descends the live click tree through private SDK types --
# FileGroup by isinstance, TaskFiles and RemoteTaskGroup by class-name STRING.
# Rename or remove one upstream and nothing raises: the walk just stops
# descending and the page comes out short. Combined with the warn-and-continue
# this function used to do, that produced a green regen and a silently gutted
# reference. Both floors exist to make that loud.
MIN_COMMANDS = 20
MIN_RETAINED_FRACTION = 0.6


def count_commands(markdown: str, cli_name: str) -> int:
    """Count rendered command sections, e.g. `### flyte run`, at any heading depth."""
    return len(re.findall(rf"^#{{2,6}} {re.escape(cli_name)}\b", markdown, re.M))


def generate_python_cli(cli: dict, python: Path) -> None:
    """Generate CLI docs for a Python-based CLI."""
    include = cli["include"]
    output_file = REPO_ROOT / cli["output_file"]
    import_name = cli.get("import", cli.get("package", cli["name"]))
    gen_command = cli["gen_command"]

    # Get version
    version_cmd = f"import {import_name}; print({import_name}.__version__)"
    result = subprocess.run(
        [str(python), "-c", version_cmd],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    version = result.stdout.strip() if result.returncode == 0 else "unknown"

    # Read include template and substitute version
    include_path = REPO_ROOT / include
    header = include_path.read_text().replace("%%VERSION%%", version)

    # Generate CLI docs
    gen_parts = shlex.split(gen_command)
    # Use the venv's binary for the first part of the command
    if not (REPO_ROOT / gen_parts[0]).exists():
        # It's a binary in the venv
        venv_bin = python.parent / gen_parts[0]
        if venv_bin.exists():
            gen_parts[0] = str(venv_bin)

    result = subprocess.run(
        gen_parts,
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        sys.exit(
            f"ERROR: CLI doc generation failed for {cli['name']} (exit {result.returncode}).\n"
            f"  command: {' '.join(gen_parts)}\n"
            f"  stderr: {result.stderr.strip() or '(empty)'}\n"
            "This used to warn and leave the previous page in place, which made a broken\n"
            "generator look like a clean regen."
        )

    # Guard against a generator that succeeds but produces almost nothing.
    new_count = count_commands(result.stdout, cli["name"])
    if new_count < MIN_COMMANDS:
        sys.exit(
            f"ERROR: {cli['name']} CLI docs rendered only {new_count} command section(s), "
            f"below the floor of {MIN_COMMANDS}.\n"
            "The generator exited 0, so this is most likely a tree-walk that stopped "
            "descending -- check whether a private SDK type it matches on was renamed."
        )
    if output_file.exists():
        old_count = count_commands(output_file.read_text(), cli["name"])
        if old_count and new_count < old_count * MIN_RETAINED_FRACTION:
            sys.exit(
                f"ERROR: {cli['name']} CLI docs dropped from {old_count} to {new_count} "
                f"command section(s), below {MIN_RETAINED_FRACTION:.0%} of the previous page.\n"
                "Commands do get removed between releases, so this is a floor rather than a\n"
                "no-change rule -- but a fall this large is far more likely a broken walk.\n"
                "If the removal is real, regenerate with the floor lowered deliberately."
            )

    # Write combined output
    tmp_file = str(output_file) + ".tmp"
    with open(tmp_file, "w") as f:
        f.write(header)
        f.write("\n")
        f.write(result.stdout)
    os.rename(tmp_file, output_file)
    print(f"  Generated {cli['output_file']}")


def generate_go_cli(cli: dict) -> None:
    """Generate CLI docs for a Go-based CLI."""
    binary = cli["binary"]
    include = cli["include"]

    # Go CLIs may use output_file (single file) or output_dir (multi-file, pre-committed)
    if "output_dir" in cli:
        output_dir = REPO_ROOT / cli["output_dir"]
        if output_dir.is_dir():
            print(f"  Skipping {binary}: output_dir already exists ({cli['output_dir']})")
        else:
            print(f"  Warning: {binary} output_dir missing ({cli['output_dir']}), "
                  f"but Go CLI generation is not supported on this branch")
        return

    output_file = REPO_ROOT / cli["output_file"]

    # Use the gen-cli-docs script
    gen_script = INFRA_ROOT / "scripts" / "gen-cli-docs"
    if gen_script.exists():
        result = subprocess.run(
            [str(gen_script), binary],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"  Warning: Go CLI doc generation failed: {result.stderr}", file=sys.stderr)
            return

        # Read include template
        include_path = REPO_ROOT / include
        header = include_path.read_text()

        # Write combined output
        tmp_file = str(output_file) + ".tmp"
        with open(tmp_file, "w") as f:
            f.write(header)
            f.write("\n")
            f.write(result.stdout)
        os.rename(tmp_file, output_file)
        print(f"  Generated {cli['output_file']}")
    else:
        print(f"  Warning: unionai-docs-infra/scripts/gen-cli-docs not found, skipping {binary}")


def main() -> None:
    config = load_config()
    clis = config.get("clis", [])
    skip_venv = os.environ.get("SKIP_VENV_SETUP", "false").lower() == "true"

    if not clis:
        print("No [[clis]] entries in api-packages.toml")
        return

    if skip_venv:
        python = Path(sys.executable)
    else:
        python = VENV_DIR / "bin" / "python"
        if not python.exists():
            print(f"Error: shared venv not found at {VENV_DIR}.")
            print("Run 'make -f unionai-docs-infra/Makefile.api.sdk setup-venv' first.")
            sys.exit(1)

    for cli in clis:
        if cli.get("frozen", False):
            print(f"Skipping {cli['name']}: frozen (committed content)")
            continue
        cli_type = cli.get("type", "python")
        print(f"Generating CLI docs for {cli['name']}...")

        if cli_type == "go":
            generate_go_cli(cli)
        else:
            if not skip_venv and not python.exists():
                sys.exit(
                    f"ERROR: venv not found at {VENV_DIR}, so {cli['name']} CLI docs cannot be\n"
                    "generated. Run SDK generation first, or set SKIP_VENV_SETUP=true to use\n"
                    "the current environment. This used to skip quietly and leave the page stale."
                )
            generate_python_cli(cli, python)


if __name__ == "__main__":
    main()
