#!/usr/bin/env python3
"""Guard the class links the API-reference generator writes.

The `flatten` branch used to lead its path with an extra `..`, so every link
climbed one level too far. For a class in the page's own module -- the common
case, and a link to an anchor on the page itself -- that produced
`.././<module>#anchor`.

It was invisible for a long time because it half worked. The form is wrong
source-relatively, so Hugo could not resolve it and passed it through to the
HTML untouched; the browser then resolved it against the page URL, which for a
leaf is one level deeper, and it landed back on the page by accident. The `.md`
twin resolves from the source directory and got a 404. That was 802 of the 1,649
broken links on v1. DOC-1525.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "api_generator"))

from lib.generate.classes import generate_class_link  # noqa: E402

ROOT = "/x/flytekit-sdk"


def _link(fullname, page, flatten=True):
    return generate_class_link(fullname, ROOT, f"{ROOT}/{page}", flatten)


def test_a_class_in_the_pages_own_module_links_to_this_page():
    """The regression: no leading `..`, so it stays on the module page."""
    link = _link(
        "flytekit.clients.auth.auth_client.AuthorizationClient",
        "flytekit.clients.auth.auth_client.md",
    )
    assert link.startswith("./flytekit.clients.auth.auth_client#")
    assert not link.startswith("..")
    assert ".././" not in link


def test_the_anchor_is_kept():
    link = _link(
        "flytekit.clients.auth.auth_client.AuthorizationClient",
        "flytekit.clients.auth.auth_client.md",
    )
    assert link.endswith("#flytekitclientsauthauth_clientauthorizationclient")


def test_the_link_resolves_to_its_own_page_from_the_source_dir():
    """Stated as the twin resolver sees it: base is the source directory.

    `./<module>` from `flytekit-sdk` is `flytekit-sdk/<module>`, the page
    itself. Under the old form `.././<module>` it was `<parent>/<module>`,
    which is not a page and 404'd.
    """
    page = "flytekit.clients.auth.auth_client"
    link = _link(f"{page}.AuthorizationClient", f"{page}.md")
    target = (Path(ROOT) / link.split("#")[0]).resolve()
    assert target == (Path(ROOT) / page).resolve()


def test_non_flatten_shapes_are_untouched():
    """Only the flatten branch changed; the others must not move."""
    assert _link("pkg.mod.Cls", "pkg.mod.md", flatten=False) == "./pkg.mod/cls"
