#!/usr/bin/env python3
"""Guard the markdown-twin path convention itself (DOC-1432).

`page_paths` is the single place that says where a page's markdown twin lives
and which directory a link inside it was written against. Both stages of the
LLM-docs pipeline import it; when the two stages once disagreed about that,
every cross-directory link in the per-page output shipped broken (DOC-1494).
"""

import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "llms_generator"
sys.path.insert(0, str(TOOLS))

import page_paths as P  # noqa: E402


def _built(tmp_path: Path) -> tuple[Path, Path]:
    """A source tree and the built variant tree Hugo would produce from it.

    `guide` is a section with two children; `guide/caching` is a leaf. Hugo
    gives BOTH a pretty URL, so both end up as `<name>.md` beside a directory
    of the same name -- which is exactly why the shape cannot discriminate.
    """
    content = tmp_path / "content"
    out = tmp_path / "dist" / "docs" / "v2" / "union"
    (content / "guide").mkdir(parents=True)
    (content / "guide" / "_index.md").write_text("---\ntitle: Guide\n---\n")
    (content / "guide" / "caching.md").write_text("---\ntitle: Caching\n---\n")
    (content / "guide" / "deploy").mkdir()
    (content / "guide" / "deploy" / "_index.md").write_text("---\ntitle: Deploy\n---\n")
    for url_dir in ("guide", "guide/caching", "guide/deploy"):
        (out / url_dir).mkdir(parents=True, exist_ok=True)
        (out / url_dir / "index.html").write_text("<html></html>")
        (out / (url_dir + ".md")).write_text("# page\n")
    P.root_page(out).write_text("# Documentation\n")
    (out / "guide" / P.RETIRED_BUNDLE_NAME).write_text("# bundle\n")
    return content, out


def test_the_twin_sits_beside_the_page_directory_not_inside_it(tmp_path):
    _content, out = _built(tmp_path)
    assert P.page_for(out, "guide/caching") == out / "guide" / "caching.md"
    assert P.page_for(out, "guide") == out / "guide.md"


def test_the_variant_root_has_no_twin(tmp_path):
    """Its twin would land outside the variant tree (DOC-1432 A2)."""
    _content, out = _built(tmp_path)
    assert P.page_for(out, "") == P.root_page(out)
    assert not P.root_page(out).name.endswith(P.PAGE_SUFFIX)
    assert not (out.parent / (out.name + ".md")).exists()


def test_url_dir_round_trips(tmp_path):
    _content, out = _built(tmp_path)
    for url_dir in ("", "guide", "guide/caching", "guide/deploy"):
        assert P.url_dir_of(out, P.page_for(out, url_dir)) == url_dir


def test_bundles_are_not_twins(tmp_path):
    _content, out = _built(tmp_path)
    names = {p.name for p in P.iter_page_twins(out)}
    assert P.RETIRED_BUNDLE_NAME not in names
    assert names == {"guide.md", "caching.md", "deploy.md"}
    # The root intermediate is not a twin either, but iter_pages yields it.
    assert P.root_page(out) not in set(P.iter_page_twins(out))
    assert P.root_page(out) in set(P.iter_pages(out))


def test_a_same_named_sibling_directory_does_NOT_mean_section(tmp_path):
    """The trap: in the BUILT tree every page has one, section or not.

    Hugo renders a leaf page to `<name>/index.html`, so `guide/caching.md` sits
    beside a directory `guide/caching/` exactly as `guide.md` sits beside
    `guide/`. A discriminator built on that shape calls every page a section
    and silently resolves every leaf page's links one level too deep.
    """
    _content, out = _built(tmp_path)
    assert (out / "guide" / "caching").is_dir()   # the trap
    assert (out / "guide").is_dir()


def test_section_landings_and_leaves_get_opposite_source_dirs(tmp_path):
    content, out = _built(tmp_path)
    # Leaf: the twin already sits in its source directory.
    assert P.source_dir_of(out, out / "guide" / "caching.md", content) == out / "guide"
    # Section landing: the source directory is one level DEEPER than the file.
    assert P.source_dir_of(out, out / "guide.md", content) == out / "guide"
    assert P.source_dir_of(out, out / "guide" / "deploy.md", content) == out / "guide" / "deploy"
    # The root is a section too (`content/_index.md`).
    assert P.source_dir_of(out, P.root_page(out), content) == out


def test_is_section_landing_reads_the_source_tree(tmp_path):
    content, out = _built(tmp_path)
    assert P.is_section_landing(content, "guide")
    assert P.is_section_landing(content, "guide/deploy")
    assert not P.is_section_landing(content, "guide/caching")
    assert P.is_section_landing(content, "")


def test_stage_one_writes_twins_and_no_root_twin(tmp_path):
    """End-to-end over process_shortcodes.py: the naming contract it emits."""
    content = tmp_path / "content"
    (content / "guide").mkdir(parents=True)
    (content / "guide" / "_index.md").write_text("---\ntitle: Guide\n---\n")
    (content / "guide" / "caching.md").write_text("---\ntitle: Caching\n---\n")
    (content / "_index.md").write_text("---\ntitle: Home\n---\n")

    tmp_md = tmp_path / "tmp-md"
    for url_dir in ("guide", "guide/caching"):
        (tmp_md / url_dir).mkdir(parents=True, exist_ok=True)
        (tmp_md / url_dir / "index.txt").write_text("# Page\n\n[Caching](caching)\n")

    out = tmp_path / "dist" / "docs" / "v2" / "union"
    out.mkdir(parents=True)

    subprocess.run(
        [sys.executable, str(TOOLS / "process_shortcodes.py"),
         "--variant=union", "--version=v2",
         f"--input-dir={tmp_md}", f"--output-dir={out}",
         f"--base-path={tmp_path}", "--quiet"],
        check=True, cwd=tmp_path)

    assert (out / "guide.md").is_file()
    assert (out / "guide" / "caching.md").is_file()
    assert not (out / "page.md").exists()
    assert not (tmp_path / "dist" / "docs" / "v2" / "union.md").exists()
    assert list(out.rglob("page.md")) == []
    # Links come out relative to the SOURCE directory, which for the section
    # landing `guide.md` is `guide/` -- one level deeper than the file sits.
    # Stage 2 resolves with the same base, so the two stages agree.
    assert "[Caching](caching.md)" in (out / "guide.md").read_text()
