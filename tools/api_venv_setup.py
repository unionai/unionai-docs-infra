#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Create a shared .venv with packages needed for SDK and CLI doc generation.

Reads api-packages.toml and installs SDK packages plus any extra CLI
dependencies into a single venv. This venv is used by api_sdk_generate.py
and api_cli_generate.py. Plugin API docs use isolated per-plugin venvs.

Set FLYTE_SDK_PATH to a local flyte-sdk checkout to use it instead of PyPI.
Any SDK package and CLI extra packages found under <FLYTE_SDK_PATH>/plugins/
will be installed from local source.
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
CLI_EXTRA_PACKAGES = {
    "flyte": ["flyteplugins-union"],
}


def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _substitute_local_flyte(packages: list, sdk_path: str) -> list:
    """Replace PyPI flyte and plugin packages with local paths, preserving extras.

    When FLYTE_SDK_PATH points to a local flyte-sdk checkout, this substitutes:
      - ``flyte[extras]`` -> ``<sdk_path>[extras]``
      - ``flyteplugins-<name>[extras]`` -> ``<sdk_path>/plugins/<name>[extras]``
        (only if the plugin directory exists locally)
    """
    sdk = Path(sdk_path)
    plugins_dir = sdk / "plugins"
    result = []
    for pkg in packages:
        if pkg == "flyte" or pkg.startswith("flyte["):
            extras = pkg[len("flyte"):]  # e.g. "[connector,aiosqlite,tui]" or ""
            local_spec = f"{sdk_path}{extras}"
            print(f"  Using local flyte-sdk: {local_spec}")
            result.append(local_spec)
        elif pkg.startswith("flyteplugins-"):
            # Extract plugin name and extras: "flyteplugins-vllm[extra]" -> ("vllm", "[extra]")
            rest = pkg[len("flyteplugins-"):]
            bracket = rest.find("[")
            if bracket >= 0:
                plugin_name = rest[:bracket]
                extras = rest[bracket:]
            else:
                plugin_name = rest
                extras = ""
            local_plugin = plugins_dir / plugin_name
            if local_plugin.is_dir():
                local_spec = f"{local_plugin}{extras}"
                print(f"  Using local plugin: {local_spec}")
                result.append(local_spec)
            else:
                result.append(pkg)
        else:
            result.append(pkg)
    return result


def _dedupe(packages: list[str]) -> list[str]:
    """Preserve the first occurrence of each install spec."""
    seen = set()
    result = []
    for pkg in packages:
        if pkg in seen:
            continue
        seen.add(pkg)
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

    # Extra packages needed by Python CLI doc generation.
    for cli in config.get("clis", []):
        if cli.get("frozen"):
            continue
        if cli.get("type", "python") == "go":
            continue
        packages.extend(CLI_EXTRA_PACKAGES.get(cli["name"], []))

    # Handle local flyte-sdk override
    flyte_sdk_path = os.environ.get("FLYTE_SDK_PATH")
    if flyte_sdk_path:
        packages = _substitute_local_flyte(packages, flyte_sdk_path)

    packages = _dedupe(packages)

    if not packages:
        print("No packages to install.")
        return

    # Create fresh venv
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    print("Creating shared venv...")
    subprocess.run(["uv", "venv", "--python", "3.12", str(VENV_DIR)], check=True)

    # Install SDK packages and CLI extras.
    print(f"Installing {len(packages)} packages: {' '.join(packages)}")
    subprocess.run([
        "uv", "pip", "install",
        "--python", str(VENV_DIR / "bin" / "python"),
        "--upgrade", *packages,
    ], check=True)
    print("Shared venv setup complete.")


if __name__ == "__main__":
    main()
