#!/usr/bin/env python3
"""HTML comments must not reach the page twins.

Hugo does not render `<!-- ... -->`, so it is absent from the page a reader sees.
It was landing in the twin, which is the page an agent sees. What leaked was
editorial scaffolding ("TODO: Add screenshot"), and the links inside it were
absolutized like any other link, so the twin offered URLs that resolve to
nothing and that no reader can see. DOC-1525.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "llms_generator"))

from build_llm_docs import LLMDocBuilder  # noqa: E402


def _twin(tmp_path, body):
    d = tmp_path / "dist" / "docs" / "v1" / "union"
    d.mkdir(parents=True)
    (d / "page.md").write_text(body, encoding="utf-8")
    b = LLMDocBuilder.__new__(LLMDocBuilder)
    b.base_path = tmp_path
    b.version = "v1"
    b.quiet = True
    b.strip_html_comments("union", "v1")
    return (d / "page.md").read_text(encoding="utf-8")


def test_multi_line_comment_and_its_links_go(tmp_path):
    out = _twin(tmp_path, "# T\n\nkeep me\n\n<!-- TODO: Add screenshot\n[Quotas](../x.png)\n-->\n\ntail\n")
    assert "TODO" not in out
    assert "../x.png" not in out
    assert "keep me" in out and "tail" in out


def test_one_line_comment_goes(tmp_path):
    out = _twin(tmp_path, "# T\n\nbefore\n<!-- hidden note -->\nafter\n")
    assert "hidden note" not in out
    assert "before" in out and "after" in out


def test_a_comment_inside_fenced_code_is_kept(tmp_path):
    """It is part of the example, not scaffolding."""
    out = _twin(tmp_path, "# T\n\n```html\n<!-- this is the example -->\n```\n")
    assert "this is the example" in out


def test_an_unterminated_comment_does_not_eat_the_rest_of_the_page(tmp_path):
    out = _twin(tmp_path, "# T\n\n<!-- oops never closed\n\nimportant tail\n")
    assert "important tail" in out
