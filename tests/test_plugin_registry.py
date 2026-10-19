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
OURS = {"organization": None, "roles": [{"role": "Owner", "user": "flyte-bot"}]}
THEIRS = {"organization": None, "roles": [{"role": "Owner", "user": "flyte-bot"}, {"role": "Owner", "user": "someone"}]}


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
        "plugins/squat/pyproject.toml": "flyteplugins-squat",
    }
    projects = {
        "flyteplugins-dbt": {"releases": {"0.0.0a0": LIVE, "2.11.0": LIVE}, "ownership": OURS},
        "flyteplugins-new": {"releases": {"0.0.0a0": LIVE}, "ownership": OURS},
        "flyteplugins-soon": None,
        "flyteplugins-squat": {"releases": {"2.11.0": LIVE}, "ownership": THEIRS},
    }
    tree = sorted(names) + [
        "plugins/dbt/src/flyteplugins/dbt/__init__.py",
        "plugins/squat/src/flyteplugins/squat/__init__.py",
    ]
    monkeypatch.setattr(cpr, "get_pypi_latest", lambda pkg: "2.11.0")
    monkeypatch.setattr(cpr, "list_tree", lambda repo, ref: tree)
    monkeypatch.setattr(cpr, "read_package_name", lambda repo, ref, p: names[p])
    monkeypatch.setattr(cpr, "pypi_project", lambda pkg: projects[pkg])
    config = {
        "plugins": [{"package": "flyteplugins-dask"}],
        "plugin_discovery": {
            "repo": "flyteorg/flyte-sdk",
            "lockstep": "flyte",
            "owners": ["flyte-bot", "unionai-oss"],
            "skip": [{"package": "flyteplugins-echo", "reason": "test fixture"}],
        },
    }
    results = {r["package"]: r for r in cpr.check(config)}
    got = {k: (r["status"], r["latest"]) for k, r in results.items()}
    assert got == {
        "flyteplugins-dbt": (cpr.UNREGISTERED, "2.11.0"),
        "flyteplugins-new": (cpr.CLAIMED, "0.0.0a0"),
        "flyteplugins-soon": (cpr.UNRELEASED, None),
        "flyteplugins-squat": (cpr.UNVERIFIED, "2.11.0"),
    }
    assert results["flyteplugins-dbt"]["plugin"] == "flyteplugins.dbt"


def test_no_owner_allowlist_means_no_owner_gate(monkeypatch):
    """A registry without `owners` (v1 today) keeps the old classification."""
    monkeypatch.setattr(cpr, "get_pypi_latest", lambda pkg: "2.11.0")
    monkeypatch.setattr(cpr, "list_tree", lambda repo, ref: ["plugins/x/pyproject.toml"])
    monkeypatch.setattr(cpr, "read_package_name", lambda repo, ref, p: "flyteplugins-x")
    monkeypatch.setattr(cpr, "pypi_project", lambda pkg: {"releases": {"2.11.0": LIVE}, "ownership": THEIRS})
    config = {"plugins": [], "plugin_discovery": {"repo": "o/r", "lockstep": "flyte"}}
    assert cpr.check(config)[0]["status"] == cpr.UNREGISTERED


def test_unresolvable_lockstep_is_an_error(monkeypatch):
    monkeypatch.setattr(cpr, "get_pypi_latest", lambda pkg: None)
    config = {"plugin_discovery": {"repo": "org/repo", "lockstep": "flyte"}}
    with pytest.raises(cpr.DiscoveryError):
        cpr.check(config)


# --- The owner gate -------------------------------------------------------------
#
# PyPI publishes no attestations for our packages, so "it's ours" rests on the
# project's owner accounts, which the JSON API reports under `ownership`. Every
# one of our 50 published packages is owned only by flyte-bot and/or unionai-oss.


@pytest.mark.parametrize(
    "ownership, expected",
    [
        (OURS, True),
        ({"organization": None, "roles": [{"role": "Owner", "user": "unionai-oss"}, {"role": "Maintainer", "user": "flyte-bot"}]}, True),
        # One outside owner is enough to withhold registration.
        (THEIRS, False),
        # An organization counts as an owner too.
        ({"organization": {"name": "evil-corp"}, "roles": [{"role": "Owner", "user": "flyte-bot"}]}, False),
        # Nothing to verify against is not a pass.
        ({"organization": None, "roles": []}, False),
        (None, False),
    ],
)
def test_owner_is_verified_only_when_every_owner_is_allowed(ownership, expected):
    assert cpr.owner_verified(ownership, {"flyte-bot", "unionai-oss"}) is expected


# --- Deriving an entry -----------------------------------------------------------

TREE = [
    "plugins/gitlab/pyproject.toml",
    "plugins/gitlab/src/flyteplugins/gitlab/__init__.py",
    "plugins/gitlab/src/flyteplugins/gitlab/_webhook.py",
    "plugins/gitlab/tests/__init__.py",
    "plugins/agents/claude/pyproject.toml",
    "plugins/agents/claude/src/flyteplugins/agents/claude/__init__.py",
    "plugins/agents/claude/src/flyteplugins/agents/claude/sub/__init__.py",
    "plugins/typesafe-ai/pyproject.toml",
    "plugins/typesafe-ai/src/flyteplugins/typesafe_ai/__init__.py",
    "plugins/weird/pyproject.toml",
    "plugins/weird/weird.py",
    # omlx and vllm ship a stray flyteplugins/__init__.py; the namespace is not the plugin.
    "plugins/omlx/pyproject.toml",
    "plugins/omlx/src/flyteplugins/__init__.py",
    "plugins/omlx/src/flyteplugins/omlx/__init__.py",
]


@pytest.mark.parametrize(
    "source, expected",
    [
        ("plugins/gitlab", "flyteplugins.gitlab"),
        # Namespace packages nest; the shallowest __init__ is the plugin module.
        ("plugins/agents/claude", "flyteplugins.agents.claude"),
        # The import path is not the package name with dashes swapped.
        ("plugins/typesafe-ai", "flyteplugins.typesafe_ai"),
        ("plugins/weird", None),
        ("plugins/omlx", "flyteplugins.omlx"),
    ],
)
def test_import_path_comes_from_the_source_tree(source, expected):
    assert cpr.import_path(TREE, source) == expected


def test_new_entry_derives_the_mechanical_fields_and_flags_the_rest():
    entry = cpr.new_entry("flyteplugins-agents-claude", "flyteplugins.agents.claude")
    assert entry == {
        "package": "flyteplugins-agents-claude",
        "plugin": "flyteplugins.agents.claude",
        "title": "Agents Claude",
        "name": "agents-claude",
    }


# --- Writing the registry --------------------------------------------------------

REGISTRY = """\
# Packages the API reference is generated from.

[[sdks]]
package = "flyte"

# Keep this list sorted by hand -- it is how the nav is ordered.
[[plugins]]
package = "flyteplugins-dask"   # a trailing comment
plugin = "flyteplugins.dask"
title = "Dask"
name = "dask"

[plugins_config]
output_base = "content/api-reference/integrations"

[plugin_discovery]
repo = "flyteorg/flyte-sdk"
owners = ["flyte-bot"]
"""


def test_register_appends_entries_and_preserves_the_file(tmp_path):
    path = tmp_path / "api-packages.toml"
    path.write_text(REGISTRY)
    cpr.register_entries(path, [cpr.new_entry("flyteplugins-gitlab", "flyteplugins.gitlab")])
    text = path.read_text()
    # Everything that was there is still there, byte for byte: the new entry
    # goes after the last [[plugins]] table and before [plugins_config].
    head, tail = REGISTRY.split("\n[plugins_config]\n")
    assert text.startswith(head.rstrip("\n"))
    assert text.endswith("\n[plugins_config]\n" + tail)
    # The new entry is marked for the human who merges the PR.
    marker = text.split('package = "flyteplugins-gitlab"')[0].rsplit("[[plugins]]", 1)[1]
    assert "auto-registered" in marker and "title" in marker
    import tomllib
    cfg = tomllib.loads(text)
    assert [p["package"] for p in cfg["plugins"]] == ["flyteplugins-dask", "flyteplugins-gitlab"]
    assert cfg["plugins"][1] == {
        "package": "flyteplugins-gitlab", "plugin": "flyteplugins.gitlab", "title": "Gitlab", "name": "gitlab",
    }


def test_register_separates_entries_with_a_blank_line_like_the_hand_written_ones(tmp_path):
    path = tmp_path / "api-packages.toml"
    path.write_text(REGISTRY)
    cpr.register_entries(path, [
        cpr.new_entry("flyteplugins-gitlab", "flyteplugins.gitlab"),
        cpr.new_entry("flyteplugins-omlx", "flyteplugins.omlx"),
    ])
    text = path.read_text()
    assert 'name = "dask"\n\n[[plugins]]\n' in text
    assert 'name = "gitlab"\n\n[[plugins]]\n' in text
    assert 'name = "omlx"\n\n[plugins_config]\n' in text


def test_register_skips_packages_already_present(tmp_path):
    path = tmp_path / "api-packages.toml"
    path.write_text(REGISTRY)
    before = path.read_text()
    written = cpr.register_entries(path, [cpr.new_entry("flyteplugins-dask", "flyteplugins.dask")])
    assert written == []
    assert path.read_text() == before


# --- What gets registered ------------------------------------------------------------


def test_registrable_takes_verified_releases_only():
    results = [
        {"package": "flyteplugins-a", "status": cpr.UNREGISTERED, "plugin": "flyteplugins.a"},
        {"package": "flyteplugins-b", "status": cpr.UNVERIFIED, "plugin": "flyteplugins.b"},
        {"package": "flyteplugins-c", "status": cpr.CLAIMED, "plugin": None},
        {"package": "flyteplugins-d", "status": cpr.UNRELEASED, "plugin": None},
    ]
    config = {"plugin_discovery": {"owners": ["flyte-bot"]}}
    assert [e["package"] for e in cpr.registrable(results, config)] == ["flyteplugins-a"]


def test_registrable_refuses_without_an_owner_allowlist():
    results = [{"package": "flyteplugins-a", "status": cpr.UNREGISTERED, "plugin": "flyteplugins.a"}]
    with pytest.raises(cpr.DiscoveryError):
        cpr.registrable(results, {"plugin_discovery": {}})


def test_registrable_refuses_a_release_whose_import_path_is_unknown():
    """No import path means no `plugin` field, and the generator would import nothing."""
    results = [{"package": "flyteplugins-a", "status": cpr.UNREGISTERED, "plugin": None}]
    with pytest.raises(cpr.DiscoveryError):
        cpr.registrable(results, {"plugin_discovery": {"owners": ["flyte-bot"]}})
