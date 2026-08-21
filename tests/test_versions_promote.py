#!/usr/bin/env python3
"""Guard `manifest.py --promote` against silently dropping versions.toml keys.

`promote_versions_toml` REBUILDS versions.toml from scratch. Any key it does not
explicitly read and re-emit is destroyed on every cut. That has now bitten twice:

  * `latest = false` (DOC-1330) — the first v1 cut dropped it, flipping the v1 line
    to the primary-line default and putting a LATEST entry in v1's menu pointing at
    /docs/latest, a URL the edge routes to v2.
  * `indexed = false` (DOC-1291) — added to versions.toml after `latest` was fixed,
    without being added to the promote path. The next v1 cut would have dropped it
    and put the whole v1 line back into Google's index.

Both failures are SILENT and SECONDARY-LINE ONLY: `main` carries neither key, so a
v2 cut round-trips clean and the bug is invisible until a v1 cut runs.

`test_promote_preserves_unknown_keys` is the generic guard: it fails for ANY
top-level key that does not survive a promote, so the third instance of this bug
fails loudly here instead of silently in production.

Usage:
    uv run pytest tests/test_versions_promote.py -v
"""

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

import manifest  # noqa: E402

DECISION = {"tag": "v1.16.26.4", "sdk": "1.16.26", "sdk_on_pypi": "true"}


def _promote(tmp_path, body: str) -> dict:
    p = tmp_path / "versions.toml"
    p.write_text(body)
    manifest.promote_versions_toml(DECISION, p)
    return tomllib.loads(p.read_text())


def test_preserves_indexed_false(tmp_path):
    """DOC-1291: the v1 line is withheld from search. A cut must not re-index it."""
    out = _promote(tmp_path, 'stable = "v1.16.26.3"\nenumerated = []\n'
                             'latest = false\nindexed = false\n')
    assert out["indexed"] is False, "promote dropped `indexed = false` — v1 goes back into Google"


def test_preserves_latest_false(tmp_path):
    """DOC-1330 regression: the original instance of this bug."""
    out = _promote(tmp_path, 'stable = "v1.16.26.3"\nenumerated = []\nlatest = false\n')
    assert out["latest"] is False


def test_absent_keys_stay_absent(tmp_path):
    """The primary line carries neither key; its file must not churn."""
    out = _promote(tmp_path, 'stable = "v2.6.2.0"\nenumerated = []\n')
    assert "latest" not in out and "indexed" not in out


def test_rotates_outgoing_stable(tmp_path):
    """The cut's actual job, asserted so the guard tests cannot pass vacuously."""
    out = _promote(tmp_path, 'stable = "v1.16.26.3"\nenumerated = []\nindexed = false\n')
    assert out["stable"] == "v1.16.26.4"
    assert "v1.16.26.3" in out["enumerated"]
    assert "v1.16.26.4" not in out["enumerated"], "the new stable is never enumerated"


def test_promote_preserves_unknown_keys(tmp_path):
    """THE generic guard. Any top-level key must survive a promote.

    `stable` and `enumerated` are the two keys promote deliberately rewrites;
    everything else must round-trip. When you add a key to versions.toml, add it to
    BOTH `promote_versions_toml` (read) and `_emit_versions_toml` (write) — this
    test is what tells you that you forgot.
    """
    body = ('stable = "v1.16.26.3"\nenumerated = []\n'
            'latest = false\nindexed = false\nsome_future_key = false\n')
    before = tomllib.loads(body)
    after = _promote(tmp_path, body)
    rewritten = {"stable", "enumerated"}
    lost = {k: v for k, v in before.items()
            if k not in rewritten and after.get(k) != v}
    assert not lost, (
        f"promote destroyed {sorted(lost)}. Add each to promote_versions_toml() "
        f"AND _emit_versions_toml() in tools/api_generator/manifest.py."
    )


# ---------------------------------------------------------------------------
# Comment preservation (DOC-1457)
#
# infra#264 made every top-level KEY round-trip. Comments still died, because the
# emitter rebuilt the file from scratch and tomllib is read-only. v1's file carries
# 29 comment lines, two of them load-bearing: why v1.16.26.2 stays un-enumerated,
# and the DOC-1291 record that the line's noindex state was previously correct only
# BY ACCIDENT and that v1 is ACTIVE, not frozen. A reader of the stripped file draws
# the opposite, wrong conclusion.
#
# promote_versions_toml now edits in place with tomlkit. These tests use v1's REAL
# file as a fixture, so they fail if the style-preserving path is ever swapped back
# for a from-scratch render.
# ---------------------------------------------------------------------------

V1_FIXTURE = Path(__file__).resolve().parent / "fixtures-v1-versions.toml"


def _promote_fixture(tmp_path) -> tuple[str, str]:
    before = V1_FIXTURE.read_text()
    p = tmp_path / "versions.toml"
    p.write_text(before)
    manifest.promote_versions_toml(DECISION, p)
    return before, p.read_text()


def test_every_comment_line_survives(tmp_path):
    before, after = _promote_fixture(tmp_path)
    lost = [l for l in before.splitlines()
            if l.strip().startswith("#") and l not in after.splitlines()]
    assert not lost, f"promote destroyed {len(lost)} comment line(s): {lost[:3]}"


def test_the_load_bearing_rationales_survive(tmp_path):
    """Named explicitly: a generic count test would pass on the wrong comments."""
    _, after = _promote_fixture(tmp_path)
    assert "v1.16.26.2 is deliberately not enumerated" in after
    assert "withheld from search so Google consolidates on v2" in after
    assert "ACTIVE, not frozen" in after


def test_keys_still_correct_alongside_comments(tmp_path):
    """The edit must still do its job, so the comment tests cannot pass vacuously."""
    _, after = _promote_fixture(tmp_path)
    d = tomllib.loads(after)
    assert d["stable"] == "v1.16.26.4"
    assert "v1.16.26.3" in d["enumerated"]
    assert "v1.16.26.4" not in d["enumerated"]
    assert d["latest"] is False and d["indexed"] is False


def test_bootstrap_still_renders_when_no_file_exists(tmp_path):
    """No file to preserve -> the from-scratch template path."""
    p = tmp_path / "versions.toml"
    manifest.promote_versions_toml(DECISION, p)
    d = tomllib.loads(p.read_text())
    assert d["stable"] == "v1.16.26.4" and d["enumerated"] == []


def test_emitter_no_longer_claims_comments_are_lost(tmp_path):
    """The bootstrap header carried a NOTE that is now false."""
    p = tmp_path / "versions.toml"
    manifest.promote_versions_toml(DECISION, p)
    assert "NOT preserved" not in p.read_text()


# ---------------------------------------------------------------------------
# The INVOCATION, not just the function (DOC-1457 follow-up)
#
# manifest.py is run by CI as a standalone script:
#
#     uv run --quiet unionai-docs-infra/tools/api_generator/manifest.py --check ...
#
# so `uv` resolves its deps from the PEP 723 `# /// script` block, NOT from
# pyproject.toml. Adding `import tomlkit` while declaring the dependency only in
# pyproject.toml left the script crashing on import under that invocation.
#
# It failed SILENTLY: regen-api-docs.yml does `eval "$(manifest.py --check ...)"`,
# so a crash yields empty output, `eval ""` succeeds, and the fold step reports
# "Not an SDK-release cut" instead of failing. The v1 1.16.28 regen therefore
# opened without its versions.toml promote and nobody was told why.
#
# Every test above passed throughout, because pytest imports the module through
# the project venv where tomlkit is present. They tested the function; nothing
# tested how CI actually calls it.
# ---------------------------------------------------------------------------

MANIFEST_PY = Path(__file__).resolve().parents[1] / "tools" / "api_generator" / "manifest.py"


def _script_deps() -> list[str]:
    """Parse the PEP 723 dependency list out of the script header."""
    import re
    block = re.search(r"# /// script\n(.*?)# ///", MANIFEST_PY.read_text(), re.S)
    assert block, "manifest.py lost its PEP 723 script block"
    body = "".join(l.lstrip("#").strip() for l in block.group(1).splitlines())
    return re.findall(r'"([^"]+)"', body)


def test_every_third_party_import_is_declared_for_uv_run():
    """Guard the whole class, not just tomlkit.

    Any third-party module imported at manifest.py's top level must appear in the
    PEP 723 block, or `uv run manifest.py` dies on import — silently, given how the
    workflow evals its output.
    """
    import ast
    import sys

    tree = ast.parse(MANIFEST_PY.read_text())
    top_level = []
    for node in tree.body:                      # module scope only
        if isinstance(node, ast.Import):
            top_level += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level.append(node.module.split(".")[0])

    declared = {d.split(";")[0].split("[")[0].split(">")[0].split("=")[0].strip().replace("-", "_")
                for d in _script_deps()}
    local = {"_versions", "_repo"}              # sys.path-injected siblings
    missing = sorted(
        m for m in set(top_level)
        if m not in sys.stdlib_module_names and m not in declared and m not in local
    )
    assert not missing, (
        f"{missing} imported at module level but absent from manifest.py's PEP 723 "
        f"block. `uv run manifest.py` will fail on import, and regen-api-docs.yml "
        f"swallows that failure via eval."
    )


def test_tomlkit_specifically_is_declared():
    """The instance that broke the v1 1.16.28 regen."""
    assert any(d.startswith("tomlkit") for d in _script_deps())


# ---------------------------------------------------------------------------
# Pin retention (DOC-1448).
#
# Cloudflare Pages refuses a deployment above 20,000 files and every enumerated
# entry materializes another full site tree (~3,400 files). Seven trees broke the
# deploy on 2026-08-21: the site did not publish for about an hour, and because
# the search-index step is gated on a successful deploy it was SKIPPED rather
# than failed, so the index went stale with no red step saying so.
#
# Retention is what stops a cut walking back into that. These tests pin the
# behaviour that matters: the count is bounded, the OLDEST go, the newest stay,
# and pruning is reported -- a pruned pin 404s until someone adds its redirect.
# ---------------------------------------------------------------------------


def test_retention_keeps_the_newest_and_drops_the_oldest():
    kept, pruned = manifest._apply_retention(["v2.6.1.0", "v2.6.2.0", "v2.6.3.0", "v2.6.4.0"], 3)
    assert kept == ["v2.6.2.0", "v2.6.3.0", "v2.6.4.0"]
    assert pruned == ["v2.6.1.0"]


def test_retention_is_a_no_op_below_the_limit():
    pins = ["v2.6.1.0", "v2.6.2.0"]
    kept, pruned = manifest._apply_retention(pins, 3)
    assert kept == pins and pruned == []


def test_retention_is_a_no_op_exactly_at_the_limit():
    # The current main state. A cut today must not silently drop a served pin.
    pins = ["v2.6.1.0", "v2.6.2.0", "v2.6.3.0"]
    kept, pruned = manifest._apply_retention(pins, 3)
    assert kept == pins and pruned == []


def test_the_next_cut_prunes_exactly_one(tmp_path):
    """The scenario retention exists for: main's real state, one cut later.

    Without retention this reaches 4 pins = 6 trees = ~20,600 files, over the cap.
    """
    body = 'stable = "v2.6.5.0"\nenumerated = [\n  "v2.6.1.0",\n  "v2.6.2.0",\n  "v2.6.3.0",\n]\n'
    out = _promote(tmp_path, body)
    assert out["stable"] == DECISION["tag"]
    # outgoing stable rotated in, oldest pruned, count held
    assert "v2.6.5.0" in out["enumerated"]
    assert "v2.6.1.0" not in out["enumerated"]
    assert len(out["enumerated"]) == 3


def test_max_enumerated_override_is_honoured(tmp_path):
    body = (
        'stable = "v2.6.5.0"\nmax_enumerated = 1\n'
        'enumerated = [\n  "v2.6.1.0",\n  "v2.6.2.0",\n  "v2.6.3.0",\n]\n'
    )
    out = _promote(tmp_path, body)
    assert len(out["enumerated"]) == 1
    assert out["max_enumerated"] == 1  # the override itself must survive the cut


def test_retention_never_drops_the_incoming_stable(tmp_path):
    """The new stable is served at /docs/<line>; enumerating it would duplicate a tree."""
    body = 'stable = "v2.6.5.0"\nenumerated = [\n  "v2.6.1.0",\n  "v2.6.2.0",\n  "v2.6.3.0",\n]\n'
    out = _promote(tmp_path, body)
    assert DECISION["tag"] not in out["enumerated"]


def test_pruning_is_reported_not_silent(tmp_path, capsys):
    """A pruned pin 404s until a redirect is added, so it cannot go unannounced."""
    body = 'stable = "v2.6.5.0"\nenumerated = [\n  "v2.6.1.0",\n  "v2.6.2.0",\n  "v2.6.3.0",\n]\n'
    _promote(tmp_path, body)
    err = capsys.readouterr().err
    assert "v2.6.1.0" in err
    assert "redirect" in err.lower()
