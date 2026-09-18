#!/usr/bin/env python3
"""Guard the rendered-image checker.

The defect this tool exists for (DOC-1515) is not "an image is missing". It is
"every checker reads a different artifact than the browser does". So the tests
that matter are the ones pinning HOW a src is resolved, not whether a file
exists.

Two of them encode real incidents:

  - unionai-docs-infra#290 rebased relative image paths on api-reference pages
    but prepended `../` unconditionally, so `_index.md` pages went one level too
    high. The wrong path landed on `/docs/v2/_static/...`, which 302s to an HTML
    page rather than 404ing -- so a status-code check would have called it fine.
    That is why `classify` treats an HTML target as a failure with its own
    reason, rather than as a hit.

  - Before #290, the same pages went one level too LOW. Both directions must
    fail, which is only true if resolution is done against the page's URL.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "image_checker"))

from check_rendered_images import (  # noqa: E402
    IMG_RE, classify, page_url_dir, resolve,
)

TOOL = Path(__file__).resolve().parent.parent / "tools" / "image_checker" / "check_rendered_images.py"


def build(tmp_path, pages, files=()):
    """A miniature built site. `pages` maps dist-relative html path -> src."""
    dist = tmp_path / "dist"
    for rel, src in pages.items():
        p = dist / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'<html><body><img src="{src}" alt="x"></body></html>')
    for rel in files:
        f = dist / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
    return dist


# --- resolution: the whole point of the tool ------------------------------

def test_relative_src_resolves_against_the_pages_url_not_the_source_dir(tmp_path):
    """A browser resolves against the page URL. check_images.sh does not, which
    is exactly why it cannot see this class of bug."""
    dist = build(tmp_path,
                 {"docs/v2/union/guide/page/index.html": "../../_static/x.png"},
                 ["docs/v2/union/_static/x.png"])
    html = dist / "docs/v2/union/guide/page/index.html"
    assert resolve("../../_static/x.png", html, dist) == dist / "docs/v2/union/_static/x.png"


def test_absolute_src_resolves_against_the_site_root(tmp_path):
    dist = build(tmp_path, {"docs/v2/union/p/index.html": "/docs/v2/union/_static/x.png"})
    html = dist / "docs/v2/union/p/index.html"
    assert resolve("/docs/v2/union/_static/x.png", html, dist) == dist / "docs/v2/union/_static/x.png"


def test_dotdot_segments_inside_an_absolute_src_are_normalized(tmp_path):
    """Hugo emits `RelPermalink + ../ + path`, so real srcs are absolute AND
    carry `..`. All 2777 images in a production build have this shape."""
    dist = build(tmp_path, {"docs/v2/union/a/b/index.html": "x"})
    html = dist / "docs/v2/union/a/b/index.html"
    got = resolve("/docs/v2/union/a/b/../../_static/x.png", html, dist)
    assert got == dist / "docs/v2/union/_static/x.png"


def test_query_string_and_fragment_are_stripped(tmp_path):
    """Every local image carries the DOC-1251 cache-buster."""
    dist = build(tmp_path, {"docs/p/index.html": "x"})
    html = dist / "docs/p/index.html"
    assert resolve("/docs/_static/x.png?v=abc123", html, dist) == dist / "docs/_static/x.png"


def test_percent_escapes_are_decoded(tmp_path):
    dist = build(tmp_path, {"docs/p/index.html": "x"})
    html = dist / "docs/p/index.html"
    assert resolve("/docs/_static/my%20image.png", html, dist) == dist / "docs/_static/my image.png"


def test_a_src_climbing_out_of_the_site_root_is_not_silently_clamped(tmp_path):
    dist = build(tmp_path, {"docs/index.html": "x"})
    html = dist / "docs/index.html"
    assert resolve("../../../etc/passwd", html, dist) is None
    assert classify(None) == "escapes"


# --- classification: why a 200 is not proof -------------------------------

def test_an_html_target_is_a_failure_with_its_own_reason(tmp_path):
    """#290's `_index.md` defect produced a path that 302s to an HTML page.
    Anything asserting only on HTTP status would have passed it."""
    dist = build(tmp_path, {"docs/p/index.html": "x"}, ["docs/other/index.html"])
    assert classify(dist / "docs/other/index.html") == "html"


def test_a_directory_target_is_a_failure(tmp_path):
    dist = build(tmp_path, {"docs/p/index.html": "x"}, ["docs/_static/a/b.png"])
    assert classify(dist / "docs/_static/a") == "directory"


def test_a_real_file_passes(tmp_path):
    dist = build(tmp_path, {"docs/p/index.html": "x"}, ["docs/_static/x.png"])
    assert classify(dist / "docs/_static/x.png") == "ok"


def test_a_missing_file_fails(tmp_path):
    dist = build(tmp_path, {"docs/p/index.html": "x"})
    assert classify(dist / "docs/_static/nope.png") == "missing"


# --- page_url_dir ---------------------------------------------------------

def test_pretty_url_base_is_the_pages_own_directory(tmp_path):
    dist = build(tmp_path, {"docs/v2/union/guide/index.html": "x"})
    assert page_url_dir(dist / "docs/v2/union/guide/index.html", dist) == "/docs/v2/union/guide"


# --- src extraction -------------------------------------------------------

def test_src_is_found_regardless_of_attribute_order_and_quoting():
    assert IMG_RE.findall('<img alt="a" src="/x.png" width="2">') == ["/x.png"]
    assert IMG_RE.findall("<img src='/y.png'>") == ["/y.png"]
    assert IMG_RE.findall('<IMG SRC = "/z.png">') == ["/z.png"]
    assert IMG_RE.findall('<image src="/no.png">') == []


def test_srcset_alone_is_not_mistaken_for_src():
    """`srcset` is a different attribute with a different grammar; matching it
    as `src` would report phantom failures on every responsive image."""
    assert IMG_RE.findall('<img srcset="/a.png 1x, /b.png 2x" src="/a.png">') == ["/a.png"]


# --- end to end -----------------------------------------------------------

def run(dist):
    return subprocess.run([sys.executable, str(TOOL), "--dist", str(dist)],
                          capture_output=True, text=True)


def test_clean_site_exits_zero(tmp_path):
    dist = build(tmp_path,
                 {"docs/v2/union/p/index.html": "/docs/v2/union/p/../_static/x.png"},
                 ["docs/v2/union/_static/x.png"])
    r = run(dist)
    assert r.returncode == 0, r.stdout
    assert "OK" in r.stdout


def test_the_290_index_defect_fails(tmp_path):
    """One `../` too many on an _index.md page."""
    dist = build(tmp_path,
                 {"docs/v2/union/api-reference/index.html":
                  "/docs/v2/union/api-reference/../../_static/x.png"},
                 ["docs/v2/union/_static/x.png"])
    r = run(dist)
    assert r.returncode == 1, r.stdout
    assert "no such file in the build" in r.stdout


def test_the_pre_290_leaf_defect_fails(tmp_path):
    """One `../` too few on a leaf page -- the bug #290 set out to fix."""
    dist = build(tmp_path,
                 {"docs/v2/union/api-reference/page/index.html": "../_static/x.png"},
                 ["docs/v2/union/_static/x.png"])
    r = run(dist)
    assert r.returncode == 1, r.stdout


def test_external_and_data_srcs_are_not_checked(tmp_path):
    dist = build(tmp_path, {"docs/a/index.html": "https://example.com/x.png",
                            "docs/b/index.html": "data:image/gif;base64,R0lGOD",
                            "docs/c/index.html": "//cdn.example.com/x.png"})
    r = run(dist)
    assert r.returncode == 0, r.stdout
    assert "3 external" in r.stdout


def test_a_missing_dist_exits_two_rather_than_passing(tmp_path):
    """The failure mode a CI gate must not have: no build, so nothing to check,
    so it 'passes'."""
    r = run(tmp_path / "nope")
    assert r.returncode == 2
    assert "make dist" in r.stderr
