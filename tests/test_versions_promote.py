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
