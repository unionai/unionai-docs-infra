#!/usr/bin/env python3
"""The link baseline reports entries that matched nothing.

A dead entry is worse than inert: it still suppresses that exact link if it ever
comes back, so the ratchet quietly loses a tooth. They accumulate on their own --
a pinned tag rolling out of the window retires every entry that existed only to
cover it -- so nobody would think to look. DOC-1525.
"""

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "link_checker" / "check_generated_links.py"
BASE = "https://www.union.ai/docs"


def _tree(tmp_path, link, baseline_lines):
    d = tmp_path / "dist" / "docs" / "v1" / "union"
    d.mkdir(parents=True)
    (d / "page.md").write_text(f"# T\n\n[x]({BASE}/v1/union/{link})\n", encoding="utf-8")
    (d / "real.md").write_text("# Real\n", encoding="utf-8")
    b = tmp_path / "baseline.txt"
    b.write_text("\n".join(baseline_lines) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--dist", str(tmp_path / "dist" / "docs"),
                        "--exclude", str(b)], capture_output=True, text=True)
    return r.stdout + r.stderr


def test_an_entry_that_matched_nothing_is_reported(tmp_path):
    out = _tree(tmp_path, "real.md", ["union/never/referenced"])
    assert "matched nothing" in out
    assert "union/never/referenced" in out


def test_an_entry_that_did_its_job_is_not_reported(tmp_path):
    """It suppressed a real broken link, so it is doing exactly its job."""
    out = _tree(tmp_path, "gone", ["union/gone"])
    assert "excluded by" in out, out          # it was used
    assert "matched nothing" not in out, out  # so it is not stale


def test_staleness_is_reported_but_never_fatal(tmp_path):
    """A stale entry is untidy, not broken -- it must not fail the build."""
    d = tmp_path / "dist" / "docs" / "v1" / "union"
    d.mkdir(parents=True)
    (d / "page.md").write_text("# T\n\nno links here\n", encoding="utf-8")
    b = tmp_path / "baseline.txt"
    b.write_text("union/entirely/unused\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--dist", str(tmp_path / "dist" / "docs"),
                        "--exclude", str(b)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "matched nothing" in r.stdout


def test_the_report_says_the_count_is_scoped_to_this_build(tmp_path):
    """A 2-tree local build cannot see what an 8-tree CI build covers.

    Without this caveat someone deletes entries that a pinned tag still needs.
    """
    out = _tree(tmp_path, "real.md", ["union/never/referenced"])
    assert "IN THIS BUILD" in out
    assert "every served tree" in out
