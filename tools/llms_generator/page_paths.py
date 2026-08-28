#!/usr/bin/env python3
"""The markdown-twin path convention, in ONE place.

Every documentation page is served as HTML at a URL directory and, alongside it,
as a plain-markdown twin. The twin for the page served at URL path `p` is the
file `p + '.md'`:

    content/a/b/foo.md      ->  served at /a/b/foo/   ->  twin  a/b/foo.md
    content/a/b/_index.md   ->  served at /a/b/       ->  twin  a/b.md
    content/_index.md       ->  served at /           ->  NO TWIN

This replaced `<dir>/page.md`, a name Hugo never emits and no agent guesses
(DOC-1432). The variant root has no twin on purpose (DOC-1432 A2): its twin
would be `/docs/<version>/<variant>.md`, outside the variant tree, and
`llms.txt` is the root's agent surface.

The root page is still RENDERED, because the whole generator walks the tree
from it -- `llms-full.txt`, the `llms.txt` index and the root `_section.md` all
start there. Stage 1 hands it to stage 2 as `ROOT_INTERMEDIATE`, a build
intermediate with no `.md` extension that stage 2 deletes once it is done. So
it is never served and never matched by a `*.md` glob.

WHY THIS MODULE EXISTS
----------------------
The rename moved the twin relative to its Hugo source, and it did NOT move it
uniformly:

    leaf page       source dir `content/a/b/`   twin `a/b/foo.md`   SAME directory
    section landing source dir `content/a/b/`   twin `a/b.md`       one level SHALLOWER

A relative link inside a twin was written by an author against the SOURCE
directory, so resolving it needs to know which of the two shapes the twin is.
`process_shortcodes.py` (stage 1) and `build_llm_docs.py` (stage 2) both resolve
those links, and when the bundle path once carried its own private copy of the
resolution rule the per-page path shipped every cross-directory link broken
(DOC-1494, 2,234 links). So the rule lives here and both import it.

THE DISCRIMINATOR IS THE SOURCE TREE, NOT THE OUTPUT SHAPE
----------------------------------------------------------
The built tree cannot tell a leaf page from a section landing. Hugo gives every
page a pretty URL, so `a/b/foo.md` sits beside a directory `a/b/foo/` holding
that page's own `index.html` -- exactly like a section landing sits beside its
children's directory. "Is there a directory of the same name next to it" is
therefore TRUE for every page in the output and discriminates nothing.

The source tree answers it outright: a section landing is a page whose Hugo
source is `content/<url dir>/_index.md`. That is the same test
`build_llm_docs._is_source_section()` has always used, so this module is where
it now lives rather than a second copy of it.
"""

from pathlib import Path
from typing import Iterator, Optional

# The one `.md` file name in the built tree that is NOT a page twin. Section
# bundles stay at `<dir>/_section.md`; the rename does not touch them.
BUNDLE_NAME = "_section.md"

PAGE_SUFFIX = ".md"

# Stage 1 -> stage 2 hand-off for the variant root, which has no twin. No `.md`
# extension, so no `*.md` glob and no markdown consumer ever sees it, and stage
# 2 unlinks it before the tree is deployed.
ROOT_INTERMEDIATE = ".llm-root"


def is_page_twin(path: Path) -> bool:
    """True for a served per-page markdown twin (not a bundle, not the root)."""
    return path.suffix == PAGE_SUFFIX and path.name != BUNDLE_NAME


def iter_page_twins(variant_dir: Path) -> Iterator[Path]:
    """Every served page twin in a built variant tree, sorted, bundles excluded."""
    for path in sorted(variant_dir.rglob("*" + PAGE_SUFFIX)):
        if is_page_twin(path):
            yield path


def root_page(variant_dir: Path) -> Path:
    """The variant root's build intermediate (see ROOT_INTERMEDIATE)."""
    return variant_dir / ROOT_INTERMEDIATE


def iter_pages(variant_dir: Path) -> Iterator[Path]:
    """Every page file stage 2 works on: the root intermediate, then the twins."""
    root = root_page(variant_dir)
    if root.is_file():
        yield root
    yield from iter_page_twins(variant_dir)


def page_for(variant_dir: Path, url_dir: str) -> Path:
    """The page file serving `url_dir` (relative to the variant root).

    `''` is the variant root, which has no twin -> the build intermediate.
    """
    url_dir = url_dir.strip("/")
    if not url_dir:
        return root_page(variant_dir)
    return variant_dir / (url_dir + PAGE_SUFFIX)


def url_dir_of(variant_dir: Path, page: Path) -> str:
    """The URL path a page file serves, relative to the variant root.

    `''` for the variant root.
    """
    if page.name == ROOT_INTERMEDIATE:
        return ""
    rel = str(page.relative_to(variant_dir))
    if rel.endswith(PAGE_SUFFIX):
        rel = rel[: -len(PAGE_SUFFIX)]
    rel = rel.replace("\\", "/").strip("/")
    return "" if rel == "." else rel


def is_section_landing(content_root: Path, url_dir: str) -> bool:
    """True when the page at `url_dir` was rendered from a section `_index.md`.

    The variant root is a section: its source is `content/_index.md`.
    """
    url_dir = url_dir.strip("/")
    if not url_dir:
        return True
    return (content_root / url_dir / "_index.md").is_file()


def source_dir_of(variant_dir: Path, page: Path, content_root: Path) -> Path:
    """The output directory a relative link inside `page` was written against.

    A relative link is authored against the directory of the Hugo SOURCE file,
    and the two page shapes put that directory in opposite places:

      * leaf `content/a/b/foo.md` -> twin `a/b/foo.md`. The source directory is
        `a/b`, which is the twin's OWN parent.
      * section `content/a/b/_index.md` -> twin `a/b.md`. The source directory
        is `a/b`, which is the page's own URL directory -- one level DEEPER
        than the file sits.

    Before the rename the relationship ran the other way (the twin sat one level
    deeper than its source for leaves and level with it for sections), which is
    why the old resolver tried the literal path and fell back one level UP. That
    guard is wrong in the new direction for every section landing, and being an
    existence check it would not have errored -- it would have silently resolved
    links to the wrong page. Hence an authoritative test instead of a fallback.
    """
    url_dir = url_dir_of(variant_dir, page)
    if is_section_landing(content_root, url_dir):
        return variant_dir / url_dir if url_dir else variant_dir
    return page.parent
