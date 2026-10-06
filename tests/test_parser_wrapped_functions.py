#!/usr/bin/env python3
"""Pin that decorated module functions are documented, not silently dropped.

`functools.lru_cache` (and any decorator that sets `__wrapped__`) turns a
function into a wrapper object that fails `inspect.isfunction`, so the parser
skipped it. flyteplugins.lance's only export, `register_lance_df_transformers`,
is `@lru_cache`-decorated, and its API reference rendered as an empty page.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.parser.packages import get_functions, get_package  # noqa: E402

PKG = "fakepkg_wrapped"

SOURCE = '''
import functools

__all__ = ["cached", "wrapped", "plain", "Klass"]


@functools.lru_cache
def cached(x: int) -> int:
    """A cached function."""
    return x


def _decorator(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        return fn(*args, **kwargs)
    return inner


@_decorator
def wrapped(name: str) -> str:
    """A wraps-decorated function."""
    return name


def plain() -> None:
    """A plain function."""


class Klass:
    """Classes are not functions, wrapped or not."""
'''


@pytest.fixture
def package(tmp_path, monkeypatch):
    (tmp_path / f"{PKG}.py").write_text(SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    info, module = get_package(PKG)
    yield info, module
    sys.modules.pop(PKG, None)


def test_decorated_functions_are_documented(package):
    info, module = package
    funcs = {f["name"]: f for f in get_functions(info, module)}
    assert sorted(funcs) == ["cached", "plain", "wrapped"]


def test_signature_and_doc_come_from_the_wrapped_function(package):
    info, module = package
    funcs = {f["name"]: f for f in get_functions(info, module)}
    assert [p["name"] for p in funcs["cached"]["params"]] == ["x"]
    assert funcs["cached"]["doc"].strip() == "A cached function."
    assert [p["name"] for p in funcs["wrapped"]["params"]] == ["name"]
