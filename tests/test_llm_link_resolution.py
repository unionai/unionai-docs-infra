#!/usr/bin/env python3
"""Guard the relative-link resolution in the LLM-facing `page.md` output.

The output tree has two shapes and they need different resolution bases:

  * a leaf page `foo.md` is written out as `foo/page.md`, one directory DEEPER
    than the source file the link was written in;
  * a section index `dir/_index.md` is written out as `dir/page.md`, which is
    already at the source file's own depth.

`absolutize_links()` resolved every link from the output file's own directory,
so every cross-directory link on a leaf page came out one level too deep and
404'd -- 17 of 17 sampled links, on the surface `llms.txt` and Ask AI retrieval
read (DOC-1494). The naive repair (always go up one) breaks the section-index
half instead, which is why these tests assert BOTH shapes, and why they assert
the `_index` case that resolves in neither.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import LLMDocBuilder  # noqa: E402

BASE = "https://www.union.ai/docs/v2/union"


def _page(root: Path, rel: str, body: str = "") -> Path:
    """Write a page.md at `rel` in the built variant tree."""
    f = root / rel / "page.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    return f


def _tree(tmp_path: Path) -> tuple[LLMDocBuilder, Path]:
    root = tmp_path / "dist" / "docs" / "v2" / "union"
    for rel in (
        "user-guide/tasks/task-configuration",           # section index
        "user-guide/tasks/task-configuration/caching",   # leaf
        "user-guide/tasks/task-programming/traces",      # leaf
        "user-guide/tasks/task-deployment",              # section index
    ):
        _page(root, rel)
    builder = LLMDocBuilder(tmp_path, quiet=True)
    builder.version = "v2"
    return builder, root


def test_leaf_page_resolves_against_its_source_directory(tmp_path):
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


def test_section_index_keeps_its_own_directory_as_the_base(tmp_path):
    """The regression the existence guard exists to prevent."""
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
    """Neither base exists, so the deeper (literal) resolution stands.

    The fallback must not invent a target: a link that resolves nowhere stays
    a visible 404 rather than silently landing on some other page.
    """
    builder, root = _tree(tmp_path)
    leaf = _page(
        root,
        "user-guide/tasks/task-configuration/caching",
        "[Gone](../nonexistent-section)\n",
    )
    builder.absolutize_links("union")
    assert leaf.read_text(encoding="utf-8") == (
        f"[Gone]({BASE}/user-guide/tasks/task-configuration/nonexistent-section)\n"
    )


def test_resolve_from_source_dir_covers_both_output_shapes(tmp_path):
    builder, root = _tree(tmp_path)
    leaf = root / "user-guide/tasks/task-configuration/caching/page.md"
    index = root / "user-guide/tasks/task-configuration/page.md"

    assert builder._resolve_from_source_dir(leaf, "../task-programming/traces") == (
        root / "user-guide/tasks/task-programming/traces"
    )
    assert builder._resolve_from_source_dir(index, "caching") == (
        root / "user-guide/tasks/task-configuration/caching"
    )
    assert builder._resolve_from_source_dir(index, "../task-deployment/_index") == (
        root / "user-guide/tasks/task-deployment"
    )
