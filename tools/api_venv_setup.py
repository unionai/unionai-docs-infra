#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Create a shared .venv with packages needed for SDK and CLI doc generation.

Reads api-packages.toml and installs SDK packages plus any extra CLI-only
packages into a single venv (used by api_sdk_generate.py and api_cli_generate.py).
Plugin API docs use isolated per-plugin venvs.

SDK install specs are installed first at full dependency resolution. CLI-only
extras (see CLI_EXTRA_PACKAGES) are installed afterward with ``--no-deps`` so
their PyPI metadata cannot pin an older flyte than the SDK line.

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
    sdk_packages: list[str] = []
    cli_extra_packages: list[str] = []

    # SDK packages (resolved together so flyte matches the SDK docs we generate).
    for sdk in config.get("sdks", []):
        if sdk.get("frozen"):
            continue
        install_spec = sdk.get("install", sdk["package"])
        sdk_packages.extend(shlex.split(install_spec))

    # Extra packages needed by Python CLI doc generation (e.g. flyteplugins-union for
    # `flyte gen docs --plugin-variants union`). Those plugins often declare an upper
    # bound on flyte (e.g. flyte<2.2.0); installing them in the same `uv pip install`
    # as flyte would downgrade flyte to satisfy the plugin. Install them afterward
    # with --no-deps so the SDK venv keeps the latest flyte from PyPI.
    for cli in config.get("clis", []):
        if cli.get("frozen"):
            continue
        if cli.get("type", "python") == "go":
            continue
        cli_extra_packages.extend(CLI_EXTRA_PACKAGES.get(cli["name"], []))

    flyte_sdk_path = os.environ.get("FLYTE_SDK_PATH")
    if flyte_sdk_path:
        sdk_packages = _substitute_local_flyte(sdk_packages, flyte_sdk_path)
        cli_extra_packages = _substitute_local_flyte(cli_extra_packages, flyte_sdk_path)

    sdk_packages = _dedupe(sdk_packages)
    cli_extra_packages = _dedupe(cli_extra_packages)

    if not sdk_packages and not cli_extra_packages:
        print("No packages to install.")
        return

    # Create fresh venv
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    print("Creating shared venv...")
    subprocess.run(["uv", "venv", "--python", "3.12", str(VENV_DIR)], check=True)

    py = str(VENV_DIR / "bin" / "python")

    if sdk_packages:
        print(f"Installing SDK packages: {' '.join(sdk_packages)}")
        subprocess.run(
            ["uv", "pip", "install", "--python", py, "--upgrade", *sdk_packages],
            check=True,
        )

    if cli_extra_packages:
        print(
            "Installing CLI extra packages (no-deps; avoids plugin pins downgrading flyte): "
            f"{' '.join(cli_extra_packages)}"
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                py,
                "--upgrade",
                *cli_extra_packages,
                "--no-deps",
            ],
            check=True,
        )

    print("Shared venv setup complete.")


if __name__ == "__main__":
    main()
