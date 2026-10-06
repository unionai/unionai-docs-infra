#!/usr/bin/env python3
"""Guard `check_plugin_registry.py`'s classification of discovered plugins.

The check exists because eight released flyte-sdk plugins went undocumented
with nothing noticing. Its one judgement call is telling a real release from a
PyPI name claim: names are claimed ahead of release with a `0.0.0a0`
pre-release whose wheel holds the real code (flyteplugins-dbt and
flyteplugins-slurm, October 2026), so only the version separates the two. These
tests pin that rule, and that an incomplete scan never reads as a clean one.

Usage:
    uv run pytest tests/test_plugin_registry.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

import check_plugin_registry as cpr  # noqa: E402

LIVE = [{"yanked": False}]
YANKED = [{"yanked": True}]


@pytest.mark.parametrize(
    "releases, expected",
    [
        # No PyPI project at all.
        (None, cpr.UNRELEASED),
        # The claim pattern: one 0.0.0a0 pre-release, real code inside.
        ({"0.0.0a0": LIVE}, cpr.CLAIMED),
        # Claim followed by the first real release (flyteplugins-dbt today).
        ({"0.0.0a0": LIVE, "2.11.0": LIVE}, cpr.UNREGISTERED),
        # Bare-version placeholders are final releases, but not in the 2.x line.
        ({"0.0.0": LIVE}, cpr.CLAIMED),
        ({"0.0.1": LIVE}, cpr.CLAIMED),
        # Pre-releases and dev builds in the right line are not releases yet.
        ({"2.11.0rc1": LIVE}, cpr.CLAIMED),
        ({"2.11.0b3": LIVE}, cpr.CLAIMED),
        ({"2.11.0.dev4": LIVE}, cpr.CLAIMED),
        # A yanked or file-less release does not count.
        ({"2.11.0": YANKED}, cpr.CLAIMED),
        ({"2.11.0": []}, cpr.CLAIMED),
        # A release in an older major line is not one in this line.
        ({"1.16.0": LIVE}, cpr.CLAIMED),
        # Unparseable version strings are skipped, not fatal.
        ({"not-a-version": LIVE, "2.0.0": LIVE}, cpr.UNREGISTERED),
    ],
)
def test_classify(releases, expected):
    assert cpr.classify(releases, lockstep_major=2) == expected


def test_registered_includes_frozen_and_sdks():
    config = {
        "sdks": [{"package": "flyte"}],
        "plugins": [
            {"package": "flyteplugins-dask"},
            {"package": "flyteplugins-openai", "frozen": True},
        ],
    }
    assert cpr.registered_packages(config) == {
        "flyte",
        "flyteplugins-dask",
        "flyteplugins-openai",
    }


def test_no_discovery_table_skips():
    assert cpr.check({"plugins": []}) is None


def test_truncated_tree_is_an_error(monkeypatch):
    monkeypatch.setattr(cpr, "_get_json", lambda url, token=None: {"truncated": True, "tree": []})
    with pytest.raises(cpr.DiscoveryError, match="truncated"):
        cpr.discover_pyprojects("org/repo", "main", "plugins")


def test_discovery_is_recursive_and_scoped(monkeypatch):
    tree = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "plugins/dask/pyproject.toml"},
            {"type": "blob", "path": "plugins/agents/claude/pyproject.toml"},
            {"type": "tree", "path": "plugins/agents"},
            {"type": "blob", "path": "pyproject.toml"},
            {"type": "blob", "path": "examples/plugins/pyproject.toml"},
            {"type": "blob", "path": "plugins-extra/x/pyproject.toml"},
        ],
    }
    monkeypatch.setattr(cpr, "_get_json", lambda url, token=None: tree)
    assert cpr.discover_pyprojects("org/repo", "main", "plugins") == [
        "plugins/agents/claude/pyproject.toml",
        "plugins/dask/pyproject.toml",
    ]


def test_check_end_to_end(monkeypatch):
    names = {
        "plugins/dask/pyproject.toml": "flyteplugins-dask",
        "plugins/dbt/pyproject.toml": "flyteplugins-dbt",
        "plugins/echo/pyproject.toml": "flyteplugins-echo",
        "plugins/new/pyproject.toml": "flyteplugins-new",
        "plugins/soon/pyproject.toml": "flyteplugins-soon",
    }
    releases = {
        "flyteplugins-dbt": {"0.0.0a0": LIVE, "2.11.0": LIVE},
        "flyteplugins-new": {"0.0.0a0": LIVE},
        "flyteplugins-soon": None,
    }
    monkeypatch.setattr(cpr, "get_pypi_latest", lambda pkg: "2.11.0")
    monkeypatch.setattr(cpr, "discover_pyprojects", lambda repo, ref, path: sorted(names))
    monkeypatch.setattr(cpr, "read_package_name", lambda repo, ref, p: names[p])
    monkeypatch.setattr(cpr, "pypi_releases", lambda pkg: releases[pkg])
    config = {
        "plugins": [{"package": "flyteplugins-dask"}],
        "plugin_discovery": {
            "repo": "flyteorg/flyte-sdk",
            "lockstep": "flyte",
            "skip": [{"package": "flyteplugins-echo", "reason": "test fixture"}],
        },
    }
    got = {r["package"]: (r["status"], r["latest"]) for r in cpr.check(config)}
    assert got == {
        "flyteplugins-dbt": (cpr.UNREGISTERED, "2.11.0"),
        "flyteplugins-new": (cpr.CLAIMED, "0.0.0a0"),
        "flyteplugins-soon": (cpr.UNRELEASED, None),
    }


def test_unresolvable_lockstep_is_an_error(monkeypatch):
    monkeypatch.setattr(cpr, "get_pypi_latest", lambda pkg: None)
    config = {"plugin_discovery": {"repo": "org/repo", "lockstep": "flyte"}}
    with pytest.raises(cpr.DiscoveryError):
        cpr.check(config)
