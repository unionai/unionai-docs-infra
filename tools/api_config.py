#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomli; python_version < '3.11'",
# ]
# ///
"""Read api-packages.toml and output config values for Make or shell consumption.

Usage:
  uv run --project unionai-docs-infra tools/api_config.py <section> [<key>]

Examples:
  uv run --project unionai-docs-infra tools/api_config.py plugins_config output_base
    -> content/api-reference/integrations

  uv run --project unionai-docs-infra tools/api_config.py sdk_count
    -> 1

  uv run --project unionai-docs-infra tools/api_config.py sdk 0 output_folder
    -> content/api-reference/flyte-sdk
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


def emit(val) -> None:
    """Print one config value for shell consumption.

    Booleans print as lowercase true/false. Python's own repr is True/False,
    which silently fails every `[ "$X" = "true" ]` test a caller writes, so
    every branch below goes through here rather than printing directly.
    """
    if isinstance(val, bool):
        print("true" if val else "false")
    else:
        print(val)


def main():
    if len(sys.argv) < 2:
        print("Usage: api_config.py <section> [<index>] [<key>]", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    section = sys.argv[1]

    if section == "plugins_config":
        pc = config.get("plugins_config", {})
        if len(sys.argv) >= 3:
            key = sys.argv[2]
            emit(pc.get(key, ""))
        else:
            for k, v in pc.items():
                print(f"{k}={v}")

    elif section == "sdk_count":
        print(len(config.get("sdks", [])))

    elif section == "cli_count":
        print(len(config.get("clis", [])))

    elif section == "plugin_count":
        print(len(config.get("plugins", [])))

    elif section == "sdk":
        if len(sys.argv) < 3:
            print("Usage: api_config.py sdk <index> [<key>]", file=sys.stderr)
            sys.exit(1)
        idx = int(sys.argv[2])
        sdks = config.get("sdks", [])
        if idx >= len(sdks):
            print(f"SDK index {idx} out of range (have {len(sdks)})", file=sys.stderr)
            sys.exit(1)
        sdk = sdks[idx]
        if len(sys.argv) >= 4:
            key = sys.argv[3]
            emit(sdk.get(key, ""))
        else:
            for k, v in sdk.items():
                print(f"{k}={v}")

    elif section == "cli":
        if len(sys.argv) < 3:
            print("Usage: api_config.py cli <index> [<key>]", file=sys.stderr)
            sys.exit(1)
        idx = int(sys.argv[2])
        clis = config.get("clis", [])
        if idx >= len(clis):
            print(f"CLI index {idx} out of range (have {len(clis)})", file=sys.stderr)
            sys.exit(1)
        cli = clis[idx]
        if len(sys.argv) >= 4:
            key = sys.argv[3]
            emit(cli.get(key, ""))
        else:
            for k, v in cli.items():
                print(f"{k}={v}")

    elif section == "plugin_by_name":
        # Index lookup is fine for a loop over every plugin, but a caller that
        # already knows one plugin (Makefile.api.plugins takes NAME=) would
        # otherwise have to scan for its index just to read one field.
        if len(sys.argv) < 3:
            print("Usage: api_config.py plugin_by_name <name> [<key>]", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[2]
        plugin = next(
            (p for p in config.get("plugins", []) if p.get("name") == name), None
        )
        if plugin is None:
            print(f"No plugin named '{name}' in api-packages.toml", file=sys.stderr)
            sys.exit(1)
        if len(sys.argv) >= 4:
            emit(plugin.get(sys.argv[3], ""))
        else:
            for k, v in plugin.items():
                print(f"{k}={v}")

    elif section == "plugin":
        if len(sys.argv) < 3:
            print("Usage: api_config.py plugin <index> [<key>]", file=sys.stderr)
            sys.exit(1)
        idx = int(sys.argv[2])
        plugins = config.get("plugins", [])
        if idx >= len(plugins):
            print(f"Plugin index {idx} out of range (have {len(plugins)})", file=sys.stderr)
            sys.exit(1)
        plugin = plugins[idx]
        if len(sys.argv) >= 4:
            key = sys.argv[3]
            emit(plugin.get(key, ""))
        else:
            for k, v in plugin.items():
                print(f"{k}={v}")

    else:
        print(f"Unknown section: {section}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
