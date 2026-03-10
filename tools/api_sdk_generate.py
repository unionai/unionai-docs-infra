#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Iterate [[sdks]] from api-packages.toml and generate API docs for each.

For each SDK entry, this script:
  1. Creates a fresh venv and installs the SDK package
  2. Runs the API parser to produce a YAML intermediate
  3. Runs the API generator to produce Hugo-compatible markdown

Respects SKIP_VENV_SETUP=true env var to use the current Python instead.
Set FLYTE_SDK_PATH to a local flyte-sdk checkout to use it instead of PyPI.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from _repo import get_repo_root

REPO_ROOT = get_repo_root()
CONFIG_FILE = REPO_ROOT / "api-packages.toml"
VENV_DIR = REPO_ROOT / ".venv"


def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _substitute_local_flyte(packages: list, sdk_path: str) -> list:
    """Replace PyPI flyte package with a local path, preserving extras."""
    result = []
    for pkg in packages:
        if pkg == "flyte" or pkg.startswith("flyte["):
            extras = pkg[len("flyte"):]  # e.g. "[connector,aiosqlite,tui]" or ""
            local_spec = f"{sdk_path}{extras}"
            print(f"  Using local flyte-sdk: {local_spec}")
            result.append(local_spec)
        else:
            result.append(pkg)
    return result


def setup_venv(install_spec: str) -> None:
    """Create a fresh venv and install the package."""
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    subprocess.run(["uv", "venv", str(VENV_DIR)], check=True)
    # install_spec may contain multiple space-separated packages (with shell quoting)
    packages = shlex.split(install_spec)
    # Allow local flyte-sdk override
    flyte_sdk_path = os.environ.get("FLYTE_SDK_PATH")
    if flyte_sdk_path:
        packages = _substitute_local_flyte(packages, flyte_sdk_path)
    print(f"Installing {' '.join(packages)}...")
    subprocess.run([
        "uv", "pip", "install",
        "--python", str(VENV_DIR / "bin" / "python"),
        "--upgrade", *packages,
    ], check=True)


def run_parser(python: Path, sdk: dict) -> str:
    """Run the API parser. Returns path to the generated YAML."""
    api_data = f"/tmp/{sdk['generator_name']}.api.yaml"
    cmd = [
        str(python),
        "unionai-docs-infra/tools/api_generator/parser",
        "--package", sdk["parser_package"],
        "--output", api_data,
    ]
    print(f"Parsing {sdk['parser_package']}...")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    return api_data


def run_generator(python: Path, sdk: dict, api_data: str) -> None:
    """Run the API doc generator."""
    output_folder = sdk["output_folder"]
    output_path = REPO_ROOT / output_folder

    # Clean output folder first
    if output_path.exists():
        shutil.rmtree(output_path)

    cmd = [
        str(python),
        "unionai-docs-infra/tools/api_generator/generate",
        "--name", sdk["generator_name"],
        "--title", sdk["generator_title"],
        "--api", api_data,
        "--include", sdk["include"],
        "--output_dir", output_folder,
    ]

    if sdk.get("weight"):
        cmd.extend(["--weight", str(sdk["weight"])])
    if sdk.get("expanded"):
        cmd.extend(["--expanded", str(sdk["expanded"])])
    if sdk.get("no_flatten"):
        cmd.extend(["--no-flatten", str(sdk["no_flatten"])])
    if sdk.get("variants"):
        cmd.extend(["--variants", sdk["variants"]])

    print(f"Generating docs -> {output_folder}")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> None:
    config = load_config()
    sdks = config.get("sdks", [])
    skip_venv = os.environ.get("SKIP_VENV_SETUP", "false").lower() == "true"

    if not sdks:
        print("No [[sdks]] entries in api-packages.toml")
        return

    for sdk in sdks:
        package = sdk["package"]
        install = sdk.get("install", package)
        print(f"\n{'='*60}")
        print(f"Generating API docs for {package}")
        print(f"{'='*60}")

        if skip_venv:
            print("Using current Python (SKIP_VENV_SETUP=true)")
            python = Path(sys.executable)
        else:
            setup_venv(install)
            python = VENV_DIR / "bin" / "python"

        api_data = run_parser(python, sdk)
        run_generator(python, sdk, api_data)
        print(f"Done: {sdk['output_folder']}")

    # Note: venv is NOT cleaned up here — Makefile.api.sdk handles cleanup
    # after both sdks and clis targets complete, since clis needs the venv too.


if __name__ == "__main__":
    main()
