#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Create an isolated venv for a single plugin API doc build.

This keeps plugin dependency resolution independent from the shared SDK/CLI
environment, so one plugin's transitive dependencies do not affect another's.
"""

import argparse
import os
import shlex
import shutil
import subprocess
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


def _substitute_local_flyte(packages: list[str], sdk_path: str) -> list[str]:
    """Replace published flyte and plugin packages with local paths when possible."""
    sdk = Path(sdk_path)
    plugins_dir = sdk / "plugins"
    result = []
    for pkg in packages:
        if pkg == "flyte" or pkg.startswith("flyte["):
            extras = pkg[len("flyte"):]
            result.append(f"{sdk_path}{extras}")
            continue
        if not pkg.startswith("flyteplugins-"):
            result.append(pkg)
            continue

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
            result.append(f"{local_plugin}{extras}")
        else:
            result.append(pkg)
    return result


def resolve_plugin_packages(plugin_name: str) -> list[str]:
    config = load_config()
    plugin = next((p for p in config.get("plugins", []) if p.get("name") == plugin_name), None)
    if plugin is None:
        raise ValueError(f"No plugin named '{plugin_name}' found in api-packages.toml")

    if plugin.get("install"):
        packages = shlex.split(plugin["install"])
    elif plugin.get("extras"):
        extras = ",".join(plugin["extras"])
        packages = [f"{plugin['package']}[{extras}]"]
    else:
        packages = [plugin["package"]]

    # The parser/generator scripts import yaml directly.
    packages.append("pyyaml")

    flyte_sdk_path = os.environ.get("FLYTE_SDK_PATH")
    if flyte_sdk_path:
        packages = _substitute_local_flyte(packages, flyte_sdk_path)

    return packages


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an isolated plugin venv")
    parser.add_argument("--name", required=True, help="Plugin name from api-packages.toml")
    parser.add_argument("--venv-dir", required=True, help="Directory where the venv should be created")
    args = parser.parse_args()

    venv_dir = Path(args.venv_dir)
    packages = resolve_plugin_packages(args.name)

    if venv_dir.exists():
        shutil.rmtree(venv_dir)

    print(f"Creating isolated plugin venv for {args.name} at {venv_dir}...")
    subprocess.run(["uv", "venv", "--python", "3.12", str(venv_dir)], check=True)

    print(f"Installing {len(packages)} packages: {' '.join(packages)}")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_dir / "bin" / "python"),
            "--upgrade",
            *packages,
        ],
        check=True,
    )
    print("Plugin venv setup complete.")


if __name__ == "__main__":
    main()
