#!/usr/bin/env python3
"""Pin which public-in-code names the API reference leaves out.

A name can be reachable without a leading underscore and still not be meant for
users. flyte-sdk marked several that way only in prose ("Internal: ... Not
intended for direct use", "internal only parameter"), and the generator
documented them anyway: `File.schema_match()`, `Dir.schema_match()`, and the
`_tracker` parameter of `flyte.with_runcontext()`.

The generator now honors Sphinx autodoc's `:meta private:` field, the legacy
`Internal:` docstring prefix, and `_`-prefixed parameters.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.parser.classes import get_classes  # noqa: E402
from lib.parser.packages import get_functions, get_package  # noqa: E402

PKG = "fakepkg_visibility"

SOURCE = '''
__all__ = ["Public", "Hidden", "run", "helper"]


class Public:
    """A documented class."""

    def keep(self, x: int, _internal: bool = False) -> int:
        """Kept.

        :meta public:
        """
        return x

    def meta_private(self) -> None:
        """Hidden by the field.

        :meta private:
        """

    @classmethod
    def legacy(cls, incoming: dict) -> bool:
        """Internal: Hidden by the legacy prefix. Not intended for direct use."""
        return True

    def mentions_internal(self) -> None:
        """Kept: says Internal: mid-sentence, which is not the prefix."""

    @property
    def hidden_prop(self) -> int:
        """:meta private:"""
        return 1


class Hidden:
    """A class users should not see.

    :meta private:
    """


def run(task: str, *, _tracker: object = None) -> None:
    """Run a task."""


def helper() -> None:
    """Hidden module function.

    :meta private:
    """
'''


@pytest.fixture
def package(tmp_path, monkeypatch):
    (tmp_path / f"{PKG}.py").write_text(SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    info, module = get_package(PKG)
    yield info, module
    sys.modules.pop(PKG, None)


def test_meta_private_class_is_skipped(package):
    info, module = package
    assert sorted(get_classes(info, module)) == [f"{PKG}.Public"]


def test_private_members_are_skipped(package):
    info, module = package
    cls = get_classes(info, module)[f"{PKG}.Public"]
    methods = {m["name"] for m in cls["methods"]}
    assert "keep" in methods
    assert "mentions_internal" in methods
    assert "meta_private" not in methods
    assert "legacy" not in methods
    assert "hidden_prop" not in {p["name"] for p in cls["properties"]}


def test_meta_private_function_is_skipped(package):
    info, module = package
    assert [f["name"] for f in get_functions(info, module)] == ["run"]


def test_underscore_params_are_dropped(package):
    info, module = package
    run = get_functions(info, module)[0]
    assert [p["name"] for p in run["params"]] == ["task"]
    assert "_tracker" not in run["signature"]

    keep = next(
        m for m in get_classes(info, module)[f"{PKG}.Public"]["methods"] if m["name"] == "keep"
    )
    assert [p["name"] for p in keep["params"]] == ["self", "x"]


def test_meta_fields_are_stripped_from_rendered_text(package):
    info, module = package
    keep = next(
        m for m in get_classes(info, module)[f"{PKG}.Public"]["methods"] if m["name"] == "keep"
    )
    assert ":meta" not in keep["doc"]
    assert keep["doc"].strip() == "Kept."
