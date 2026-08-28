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

These tests assert on THE MAKE COMMANDS ISSUED, not on the list handed to
`regenerate()`. The first version of them stubbed `regenerate` and asserted it
received every item, which passed while the flag did almost nothing: `regenerate`
re-reads `outdated` itself, in three separate places, so a longer list changed
nothing. `--all` regenerated 1 plugin of 32 and reported success.

That is the failure mode worth pinning, and it is why these tests reach one layer
lower. A partial regen prints "Done" and looks exactly like a complete one; the
only evidence it was partial is which generators ran.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "api_generator"))

import check_versions  # noqa: E402

RESULTS = [
    {"name": "flyte", "package": "flyte", "type": "sdk",
     "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
    {"name": "flyte", "package": "flyte", "type": "cli",
     "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
    {"name": "spark", "package": "flyteplugins-spark", "type": "plugin",
     "plugin": "spark", "title": "Spark",
     "committed": "2.6.10", "latest": "2.6.10", "outdated": False},
    {"name": "union", "package": "flyteplugins-union", "type": "plugin",
     "plugin": "union", "title": "Union",
     "committed": "0.8.2", "latest": "0.8.3", "outdated": True},
]


@pytest.fixture
def ran(monkeypatch):
    """Run main() with the network stubbed and every subprocess recorded."""
    calls = []

    class Done:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return Done()

    monkeypatch.setattr(check_versions, "load_config", lambda: {})
    monkeypatch.setattr(check_versions, "check_all", lambda cfg: [dict(r) for r in RESULTS])
    monkeypatch.setattr(check_versions, "print_results", lambda r: None)
    monkeypatch.setattr(check_versions.subprocess, "run", fake_run)
    return calls


def plugins_built(calls):
    return sorted(a.split("=", 1)[1] for c in calls for a in c if a.startswith("PLUGIN="))


def targets(calls):
    return [c[-1] for c in calls if "Makefile.api.sdk" in " ".join(c)]


def test_all_builds_every_plugin(ran, monkeypatch):
    """The regression: --all used to build only the 1 plugin that was outdated."""
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--all"])
    check_versions.main()
    assert plugins_built(ran) == ["spark", "union"]


def test_all_builds_the_sdks_and_clis_too(ran, monkeypatch):
    """Both are up-to-date here, so neither runs under --update."""
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--all"])
    check_versions.main()
    assert "sdks" in targets(ran)
    assert "clis" in targets(ran)


def test_update_builds_only_what_is_outdated(ran, monkeypatch):
    """Existing behaviour must not shift; --all is an addition, not a change."""
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--update"])
    check_versions.main()
    assert plugins_built(ran) == ["union"]
    assert "sdks" not in targets(ran)
    assert "clis" not in targets(ran)


def test_all_still_builds_when_nothing_is_outdated(monkeypatch):
    """The case that matters: a generator change, with every version current."""
    calls = []

    class Done:
        returncode = 0
        stdout = ""

    current = [dict(r, outdated=False) for r in RESULTS]
    monkeypatch.setattr(check_versions, "load_config", lambda: {})
    monkeypatch.setattr(check_versions, "check_all", lambda cfg: current)
    monkeypatch.setattr(check_versions, "print_results", lambda r: None)
    monkeypatch.setattr(check_versions.subprocess, "run",
                        lambda cmd, **kw: (calls.append(list(cmd)), Done())[1])
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--all"])
    check_versions.main()
    assert plugins_built(calls) == ["spark", "union"]
    assert "sdks" in targets(calls)


def test_update_does_nothing_when_nothing_is_outdated(monkeypatch):
    calls = []
    current = [dict(r, outdated=False) for r in RESULTS]
    monkeypatch.setattr(check_versions, "load_config", lambda: {})
    monkeypatch.setattr(check_versions, "check_all", lambda cfg: current)
    monkeypatch.setattr(check_versions, "print_results", lambda r: None)
    class Done2:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(check_versions.subprocess, "run",
                        lambda cmd, **kw: (calls.append(list(cmd)), Done2())[1])
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--update"])
    check_versions.main()
    assert calls == [], "no generator should run, not even the venv setup"


def test_regenerate_ignores_a_caller_side_filter(monkeypatch):
    """Directly pin the trap: a longer list is NOT how you force a full regen.

    `regenerate()` re-reads `outdated` per item, so handing it everything without
    force=True regenerates only the outdated ones and reports success.
    """
    calls = []

    class Done:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(check_versions.subprocess, "run",
                        lambda cmd, **kw: (calls.append(list(cmd)), Done())[1])

    check_versions.regenerate([dict(r) for r in RESULTS])
    assert plugins_built(calls) == ["union"]

    calls.clear()
    check_versions.regenerate([dict(r) for r in RESULTS], force=True)
    assert plugins_built(calls) == ["spark", "union"]


def test_the_three_modes_stay_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--update", "--all"])
    with pytest.raises(SystemExit):
        check_versions.main()
