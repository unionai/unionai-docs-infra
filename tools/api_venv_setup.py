#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Create a shared .venv with all packages needed for API doc generation.

Reads api-packages.toml and installs SDK packages, CLI dependencies,
and all plugin packages into a single venv. This venv is then used by
api_sdk_generate.py, api_cli_generate.py, and Makefile.api.plugins.

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


def main() -> None:
    config = load_config()
    packages = []

    # SDK packages
    for sdk in config.get("sdks", []):
        if sdk.get("frozen"):
            continue
        install_spec = sdk.get("install", sdk["package"])
        packages.extend(shlex.split(install_spec))

    # Plugin packages
    for plugin in config.get("plugins", []):
        if plugin.get("frozen"):
            continue
        if plugin.get("install"):
            packages.append(plugin["install"])
        elif plugin.get("extras"):
            extras = ",".join(plugin["extras"])
            packages.append(f"{plugin['package']}[{extras}]")
        else:
            packages.append(plugin["package"])

    # Handle local flyte-sdk override
    flyte_sdk_path = os.environ.get("FLYTE_SDK_PATH")
    if flyte_sdk_path:
        packages = _substitute_local_flyte(packages, flyte_sdk_path)

    if not packages:
        print("No packages to install.")
        return

    # Create fresh venv
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    print("Creating shared venv...")
    subprocess.run(["uv", "venv", "--python", "3.12", str(VENV_DIR)], check=True)

    # Install all packages
    print(f"Installing {len(packages)} packages: {' '.join(packages)}")
    subprocess.run([
        "uv", "pip", "install",
        "--python", str(VENV_DIR / "bin" / "python"),
        "--upgrade", *packages,
    ], check=True)
    print("Shared venv setup complete.")


if __name__ == "__main__":
    main()
