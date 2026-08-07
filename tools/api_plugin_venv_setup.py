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
import re
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

# Build artefacts carry a copy of pyproject.toml; never resolve a plugin into one.
SKIP_DIRS = frozenset({"build", "dist", ".venv", "venv", "node_modules", "__pycache__", ".git"})


def load_config() -> dict:
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _canonical(name: str) -> str:
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _local_plugin_index(plugins_dir: Path) -> dict[str, Path]:
    """Map distribution name -> source directory for every plugin under `plugins_dir`.

    The directory layout does not mirror distribution names, so a path cannot be
    derived from the package name. The agent adapters are nested one level deeper
    than the rest (`plugins/agents/deepagents` is `flyteplugins-agents-deepagents`),
    and at least one also differs by separator, because the directory doubles as a
    Python module name (`plugins/agents/pydantic_ai` is
    `flyteplugins-agents-pydantic-ai`).

    Every plugin's pyproject.toml already states its name, so read that instead of
    guessing -- this keeps working if the layout changes again.
    """
    index: dict[str, Path] = {}
    if not plugins_dir.is_dir():
        return index

    # Shallowest first, so a real source dir wins over any stale copy beneath it.
    for pyproject in sorted(plugins_dir.rglob("pyproject.toml"), key=lambda p: (len(p.parts), p)):
        if SKIP_DIRS & set(pyproject.parts):
            continue
        try:
            with open(pyproject, "rb") as f:
                name = tomllib.load(f).get("project", {}).get("name")
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if name:
            index.setdefault(_canonical(name), pyproject.parent)
    return index


def _substitute_local_flyte(packages: list[str], sdk_path: str) -> list[str]:
    """Replace published flyte and plugin packages with local paths when possible."""
    sdk = Path(sdk_path)
    plugins_dir = sdk / "plugins"
    index = _local_plugin_index(plugins_dir)
    result = []
    for pkg in packages:
        if pkg == "flyte" or pkg.startswith("flyte["):
            extras = pkg[len("flyte"):]
            result.append(f"{sdk_path}{extras}")
            continue
        if not pkg.startswith("flyteplugins-"):
            result.append(pkg)
            continue

        bracket = pkg.find("[")
        dist, extras = (pkg[:bracket], pkg[bracket:]) if bracket >= 0 else (pkg, "")

        local_plugin = index.get(_canonical(dist))
        if local_plugin is not None:
            result.append(f"{local_plugin}{extras}")
        else:
            # Falling back silently means a local edit appears to do nothing.
            print(
                f"WARNING: FLYTE_SDK_PATH is set, but '{dist}' was not found under "
                f"{plugins_dir}. Installing the published package instead -- local "
                f"changes to it will NOT appear in the generated docs.",
                file=sys.stderr,
            )
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


def _write_plugin(root: Path, rel: str, dist_name: str) -> None:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(f'[project]\nname = "{dist_name}"\nversion = "0.1"\n')


def self_test() -> None:
    """Exercise local-path resolution against a stand-in for the flyte-sdk layout."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sdk = Path(tmp)
        plugins = sdk / "plugins"
        # Flat, like plugins/otel.
        _write_plugin(plugins, "otel", "flyteplugins-otel")
        # Nested one level, like plugins/agents/deepagents.
        _write_plugin(plugins, "agents/deepagents", "flyteplugins-agents-deepagents")
        # Nested AND separator-mismatched: dir is a module name, dist is not.
        _write_plugin(plugins, "agents/pydantic_ai", "flyteplugins-agents-pydantic-ai")
        # A stale build copy must never win.
        _write_plugin(plugins, "agents/deepagents/build/lib", "flyteplugins-agents-deepagents")

        index = _local_plugin_index(plugins)
        assert index["flyteplugins-otel"] == plugins / "otel"
        assert index["flyteplugins-agents-deepagents"] == plugins / "agents/deepagents"
        assert index["flyteplugins-agents-pydantic-ai"] == plugins / "agents/pydantic_ai"

        out = _substitute_local_flyte(
            [
                "flyte",
                "flyte[mcp]",
                "flyteplugins-otel",
                "flyteplugins-agents-deepagents",
                "flyteplugins-agents-pydantic-ai",
                "flyteplugins-agents-openai[extra]",
                "pyyaml",
            ],
            str(sdk),
        )
        assert out[0] == str(sdk)
        assert out[1] == f"{sdk}[mcp]"
        assert out[2] == str(plugins / "otel")
        assert out[3] == str(plugins / "agents/deepagents")
        # Previously resolved to plugins/agents-pydantic-ai, which does not exist.
        assert out[4] == str(plugins / "agents/pydantic_ai")
        # Not present locally: falls back to the published package (and warns).
        assert out[5] == "flyteplugins-agents-openai[extra]"
        assert out[6] == "pyyaml"

        # A missing plugins/ dir must not raise.
        assert _local_plugin_index(sdk / "nope") == {}

    print("self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an isolated plugin venv")
    parser.add_argument("--name", help="Plugin name from api-packages.toml")
    parser.add_argument("--venv-dir", help="Directory where the venv should be created")
    parser.add_argument(
        "--self-test", action="store_true", help="Run local-path resolution tests and exit"
    )
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    if not args.name or not args.venv_dir:
        parser.error("--name and --venv-dir are required")

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
