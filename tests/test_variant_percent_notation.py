#!/usr/bin/env python3
"""`{{% variant %}}` gates a list item without breaking the list.

`{{< variant >}}{{< markdown >}}` renders its content as a separate block, so a
gated list item lands inside the preceding item and shows up nested. Writing the
item with the `{{% %}}` form, opening tag at the end of the previous line, keeps
it in the surrounding list:

    - Files{{% variant union %}}
    - Volumes{{% /variant %}}
    - DataFrames

The link checker and the page-twin generator parse variant blocks themselves, so
both must recognize this form or the excluded variant sees the gated item.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS / "link_checker"))
sys.path.insert(0, str(TOOLS / "llms_generator"))

from check_internal_links import extract_links  # noqa: E402
from process_shortcodes import ShortcodeProcessor  # noqa: E402

LIST = (
    "- [Files](./files){{% variant union %}}\n"
    "- [Volumes](./volumes){{% /variant %}}\n"
    "- [DataFrames](./dataframes)\n"
)


def _urls(content, variant):
    return [(line, url) for line, _, url in extract_links(content, variant=variant)]


def test_link_checker_drops_percent_block_for_excluded_variant():
    assert _urls(LIST, "flyte") == [(1, "./files"), (3, "./dataframes")]


def test_link_checker_keeps_percent_block_for_included_variant():
    assert _urls(LIST, "union") == [(1, "./files"), (2, "./volumes"), (3, "./dataframes")]


def test_link_checker_still_handles_angle_form():
    content = "{{< variant union >}}\n{{< markdown >}}\n[Volumes](./volumes)\n{{< /markdown >}}\n{{< /variant >}}\n"
    assert _urls(content, "flyte") == []
    assert _urls(content, "union") == [(3, "./volumes")]


def _twin(content, variant):
    return ShortcodeProcessor(variant).process_variant_shortcodes(content)


def test_twin_keeps_list_intact_for_included_variant():
    assert _twin(LIST, "union") == (
        "- [Files](./files)\n"
        "- [Volumes](./volumes)\n"
        "- [DataFrames](./dataframes)\n"
    )


def test_twin_drops_item_for_excluded_variant():
    assert _twin(LIST, "flyte") == "- [Files](./files)\n- [DataFrames](./dataframes)\n"


def test_twin_still_strips_angle_form():
    content = "A\n{{< variant union >}}\nUnion only.\n{{< /variant >}}\nB\n"
    assert _twin(content, "union") == "A\nUnion only.\nB\n"
    assert _twin(content, "flyte") == "A\n\nB\n"
