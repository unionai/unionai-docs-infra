#!/usr/bin/env python3
"""Pin CLI drift detection to the plugins that add commands, not just the CLI's package.

`flyteplugins-union` adds commands to `flyte` (`flyte factory`, `flyte fork`, ...).
The CLI page was only regenerated when `flyte` itself released, so `flyte factory`,
added in flyteplugins-union 0.12.0, never reached the page while flyte sat at 2.10.0.

The fix records each plugin's version in the page's `plugin_versions:` frontmatter
and compares that against PyPI. These tests pin both halves: the generator writes a
block the checker reads back, and a plugin release alone marks the page outdated.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "api_generator"))
sys.path.insert(0, str(ROOT / "tools"))

import api_cli_generate  # noqa: E402
import check_versions  # noqa: E402
from _versions import extract_frontmatter_map, extract_frontmatter_version  # noqa: E402

HEADER = "---\ntitle: \"Flyte CLI\"\nversion: 2.10.0\nweight: 3\n---\n\n# Flyte CLI\n"


def test_stamped_versions_round_trip(tmp_path):
    page = tmp_path / "flyte-cli.md"
    page.write_text(api_cli_generate._stamp_plugin_versions(HEADER, {"flyteplugins-union": "0.13.0"}))
    assert extract_frontmatter_map(page, "plugin_versions") == {"flyteplugins-union": "0.13.0"}
    assert extract_frontmatter_version(page) == "2.10.0"
    assert page.read_text().endswith("---\n\n# Flyte CLI\n")


def test_no_plugins_leaves_header_alone():
    assert api_cli_generate._stamp_plugin_versions(HEADER, {}) == HEADER


def test_plugin_release_alone_marks_cli_outdated(tmp_path, monkeypatch):
    page = tmp_path / "flyte-cli.md"
    page.write_text(api_cli_generate._stamp_plugin_versions(HEADER, {"flyteplugins-union": "0.12.1"}))
    latest = {"flyte": "2.10.0", "flyteplugins-union": "0.13.0"}
    monkeypatch.setattr(check_versions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_versions, "get_pypi_latest", latest.get)
    monkeypatch.setattr(check_versions, "CLI_EXTRA_PACKAGES", {"flyte": ["flyteplugins-union"]})

    [cli] = check_versions.check_all({"clis": [{"name": "flyte", "output_file": "flyte-cli.md"}]})

    assert cli["committed"] == cli["latest"] == "2.10.0"
    assert cli["plugins"] == [{"package": "flyteplugins-union", "committed": "0.12.1",
                               "latest": "0.13.0", "outdated": True}]
    assert cli["outdated"]


def test_unrecorded_plugin_marks_cli_outdated(tmp_path, monkeypatch):
    (tmp_path / "flyte-cli.md").write_text(HEADER)
    monkeypatch.setattr(check_versions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_versions, "get_pypi_latest", {"flyte": "2.10.0", "flyteplugins-union": "0.13.0"}.get)
    monkeypatch.setattr(check_versions, "CLI_EXTRA_PACKAGES", {"flyte": ["flyteplugins-union"]})

    [cli] = check_versions.check_all({"clis": [{"name": "flyte", "output_file": "flyte-cli.md"}]})

    assert cli["outdated"]
