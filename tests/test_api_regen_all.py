#!/usr/bin/env python3
"""Pin `check_versions.py --all` to regenerating every item, not just outdated ones.

Regeneration of the API reference is driven by the PyPI version moving. The pages
are committed, so that is the only trigger there is -- and it is the wrong one for
a change to the GENERATOR, which alters how every page renders while no package
version moves at all.

That is not hypothetical. infra#285 added a `description` and an `icon` to every
generated page. `make update-api-docs` then regenerated 2 packages out of 24,
because 2 had released since the last run. The other 22 would have carried the old
frontmatter until each happened to cut a release, which for a stable plugin can be
never.

`--all` exists for that case. These tests pin what it selects, since selecting the
wrong set is the whole failure mode and it is silent: a partial regen looks exactly
like a complete one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "api_generator"))

import check_versions  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    """Run main() with the network and the regen subprocesses replaced."""
    calls = []
    results = [
        {"name": "flyte", "package": "flyte", "type": "sdk",
         "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
        {"name": "spark", "package": "flyteplugins-spark", "type": "plugin",
         "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
        {"name": "union", "package": "flyteplugins-union", "type": "plugin",
         "committed": "0.8.2", "latest": "0.8.3", "outdated": True},
    ]
    monkeypatch.setattr(check_versions, "load_config", lambda: {})
    monkeypatch.setattr(check_versions, "check_all", lambda cfg: results)
    monkeypatch.setattr(check_versions, "print_results", lambda r: None)
    monkeypatch.setattr(check_versions, "regenerate", lambda r: calls.append(list(r)))
    return calls, results


def test_all_regenerates_every_item(captured, monkeypatch):
    calls, results = captured
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--all"])
    check_versions.main()
    assert len(calls) == 1
    assert calls[0] == results, "--all must pass every item, up-to-date ones included"


def test_update_regenerates_only_outdated(captured, monkeypatch):
    """The existing behaviour must not shift; --all is an addition, not a change."""
    calls, _ = captured
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--update"])
    check_versions.main()
    assert len(calls) == 1
    assert [r["package"] for r in calls[0]] == ["flyteplugins-union"]


def test_all_regenerates_even_when_nothing_is_outdated(monkeypatch):
    """The case that matters: a generator change, with every version current."""
    calls = []
    results = [
        {"name": "flyte", "package": "flyte", "type": "sdk",
         "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
        {"name": "spark", "package": "flyteplugins-spark", "type": "plugin",
         "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
    ]
    monkeypatch.setattr(check_versions, "load_config", lambda: {})
    monkeypatch.setattr(check_versions, "check_all", lambda cfg: results)
    monkeypatch.setattr(check_versions, "print_results", lambda r: None)
    monkeypatch.setattr(check_versions, "regenerate", lambda r: calls.append(list(r)))
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--all"])
    check_versions.main()
    assert calls == [results]


def test_update_does_nothing_when_nothing_is_outdated(monkeypatch):
    calls = []
    results = [{"name": "flyte", "package": "flyte", "type": "sdk",
                "committed": "2.6.10", "latest": "2.6.10", "outdated": False}]
    monkeypatch.setattr(check_versions, "load_config", lambda: {})
    monkeypatch.setattr(check_versions, "check_all", lambda cfg: results)
    monkeypatch.setattr(check_versions, "print_results", lambda r: None)
    monkeypatch.setattr(check_versions, "regenerate", lambda r: calls.append(list(r)))
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--update"])
    check_versions.main()
    assert calls == []


def test_the_three_modes_stay_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--update", "--all"])
    with pytest.raises(SystemExit):
        check_versions.main()
