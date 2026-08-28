#!/usr/bin/env python3
"""Guard the relative-link resolution in the LLM-facing markdown twins.

A link inside a twin was written by an author against the directory of the Hugo
SOURCE file. The twin does not always sit in that directory, and after the
DOC-1432 rename the two shapes are offset in OPPOSITE directions:

  * a leaf page `content/a/b/foo.md` is written out as `a/b/foo.md`, so its
    source directory is the twin's own parent, `a/b`;
  * a section landing `content/a/b/_index.md` is written out as `a/b.md`, so
    its source directory is `a/b` -- one level DEEPER than the file sits.

Before the rename the offset ran the other way for both shapes, and the
resolver coped by resolving literally and falling back one level UP when the
target did not exist (DOC-1494: 17 of 17 sampled links 404'd without it). That
guard cannot survive the rename -- it goes the wrong way for every section
landing, and being an existence check it would not error, it would silently
point links at the wrong page. So the shape of the output path is no longer
consulted: `page_paths.source_dir_of()` asks the Hugo source tree whether this
page came from an `_index.md`.

These tests therefore build BOTH trees, source and output, exactly as the
generator sees them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import LLMDocBuilder  # noqa: E402

BASE = "https://www.union.ai/docs/v2/union"

# (url dir, is a Hugo section) for the fixture tree.
TREE = [
    ("user-guide/tasks/task-configuration", True),           # section landing
    ("user-guide/tasks/task-configuration/caching", False),  # leaf
    ("user-guide/tasks/task-programming/traces", False),     # leaf
    ("user-guide/tasks/task-deployment", True),              # section landing
]


def _tree(tmp_path: Path) -> tuple[LLMDocBuilder, Path]:
    content = tmp_path / "content"
    root = tmp_path / "dist" / "docs" / "v2" / "union"
    for url_dir, is_section in TREE:
        src = content / url_dir
        if is_section:
            src.mkdir(parents=True, exist_ok=True)
            (src / "_index.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
        else:
            src.parent.mkdir(parents=True, exist_ok=True)
            src.with_suffix(".md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
        _page(root, url_dir)
    builder = LLMDocBuilder(tmp_path, quiet=True)
    builder.version = "v2"
    return builder, root


def _page(root: Path, url_dir: str, body: str = "") -> Path:
    """Write the twin for the page served at `url_dir`."""
    f = root / (url_dir + ".md")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    return f


def test_leaf_page_resolves_against_its_source_directory(tmp_path):
    """A leaf twin sits IN its source directory, so `../` means one level up."""
    builder, root = _tree(tmp_path)
    leaf = _page(
        root,
        "user-guide/tasks/task-configuration/caching",
        "See [Traces](../task-programming/traces) for details.\n",
    )
    builder.absolutize_links("union")
    assert leaf.read_text(encoding="utf-8") == (
        f"See [Traces]({BASE}/user-guide/tasks/task-programming/traces) for details.\n"
    )


def test_section_landing_resolves_one_level_DOWN_from_where_it_sits(tmp_path):
    """The regression the rename introduces if the old fallback is kept.

    `task-configuration.md` sits in `user-guide/tasks/`, but its links were
    written in `user-guide/tasks/task-configuration/_index.md`. Resolving from
    the file's own directory sends `caching` to `user-guide/tasks/caching` --
    which does not exist, so the old up-one fallback would then send it to
    `user-guide/caching`. Both are wrong and neither errors.
    """
    builder, root = _tree(tmp_path)
    index = _page(
        root,
        "user-guide/tasks/task-configuration",
        "- [Caching](caching)\n- [Task deployment](../task-deployment)\n",
    )
    builder.absolutize_links("union")
    assert index.read_text(encoding="utf-8") == (
        f"- [Caching]({BASE}/user-guide/tasks/task-configuration/caching)\n"
        f"- [Task deployment]({BASE}/user-guide/tasks/task-deployment)\n"
    )


def test_a_childless_section_is_still_a_section(tmp_path):
    """The discriminator cannot be "is there a directory of the same name".

    Hugo gives every page a pretty URL, so a leaf twin sits beside a directory
    holding its own `index.html` exactly as a section landing sits beside its
    children's. `task-deployment` here has no children at all and must still
    resolve as a section.
    """
    builder, root = _tree(tmp_path)
    childless = _page(
        root,
        "user-guide/tasks/task-deployment",
        "- [Caching](../task-configuration/caching)\n",
    )
    builder.absolutize_links("union")
    assert childless.read_text(encoding="utf-8") == (
        f"- [Caching]({BASE}/user-guide/tasks/task-configuration/caching)\n"
    )


def test_explicit_index_suffix_is_dropped(tmp_path):
    """`../task-deployment/_index` names a Hugo source file, not a URL."""
    builder, root = _tree(tmp_path)
    leaf = _page(
        root,
        "user-guide/tasks/task-configuration/caching",
        "See [Task deployment](../task-deployment/_index#serving).\n",
    )
    builder.absolutize_links("union")
    assert leaf.read_text(encoding="utf-8") == (
        f"See [Task deployment]({BASE}/user-guide/tasks/task-deployment#serving).\n"
    )


def test_external_anchor_and_root_relative_links_are_left_alone(tmp_path):
    builder, root = _tree(tmp_path)
    leaf = _page(
        root,
        "user-guide/tasks/task-configuration/caching",
        "[Site](https://example.com) [Here](#caching-key) [Root](/docs/v2/union/x)\n",
    )
    builder.absolutize_links("union")
    assert leaf.read_text(encoding="utf-8") == (
        "[Site](https://example.com) [Here](#caching-key) "
        "[Root](https://www.union.ai/docs/v2/union/x)\n"
    )


def test_unresolvable_link_is_not_relocated(tmp_path):
    """A link that resolves nowhere stays a visible 404.

    The resolver must not invent a target by trying a second base: silently
    landing on some other page is worse than the 404 (DOC-1499's wrong-200).
    """
    builder, root = _tree(tmp_path)
    leaf = _page(
        root,
        "user-guide/tasks/task-configuration/caching",
        "[Gone](../nonexistent-section)\n",
    )
    builder.absolutize_links("union")
    assert leaf.read_text(encoding="utf-8") == (
        f"[Gone]({BASE}/user-guide/tasks/nonexistent-section)\n"
    )


def test_resolve_from_source_dir_covers_both_page_shapes(tmp_path):
    builder, root = _tree(tmp_path)
    builder.variant_root = root
    leaf = root / "user-guide/tasks/task-configuration/caching.md"
    index = root / "user-guide/tasks/task-configuration.md"

    assert builder._resolve_from_source_dir(leaf, "../task-programming/traces") == (
        root / "user-guide/tasks/task-programming/traces"
    )
    assert builder._resolve_from_source_dir(index, "caching") == (
        root / "user-guide/tasks/task-configuration/caching"
    )
    assert builder._resolve_from_source_dir(index, "../task-deployment/_index") == (
        root / "user-guide/tasks/task-deployment"
    )
