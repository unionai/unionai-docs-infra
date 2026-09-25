#!/usr/bin/env python3
"""Pin which namespaces `--all-only` documents, with and without `--subpackages`.

Plugins are parsed with `--all-only`, which documented only the top-level __all__.
flyteplugins-union also exposes `flyteplugins.union.factory`, `.io`, `.remote` and
`.utils`, each with its own __all__, and none of them reached the API reference:
0.13.0 changed `Factory.materialize` and the regen was a version-stamp bump.

`--subpackages` (opt-in via `document_subpackages` in api-packages.toml) adds every
public submodule that declares its own __all__, and skips the rest, such as a `cli`
package of Click commands or a `_private` one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "api_generator"))

from lib.parser.packages import get_all_only  # noqa: E402

PKG = "fakeplugin_subpkgs"


@pytest.fixture
def fake_package(tmp_path, monkeypatch):
    root = tmp_path / PKG
    files = {
        "__init__.py": "__all__ = ['top']\ndef top(): pass\n",
        "factory/__init__.py": "__all__ = ['Factory']\nclass Factory: pass\n",
        "cli/__init__.py": "def command(): pass\n",
        "_private/__init__.py": "__all__ = ['hidden']\ndef hidden(): pass\n",
    }
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def names(result):
    return [info["name"] for info, _ in result]


def test_default_documents_top_level_only(fake_package):
    assert names(get_all_only(PKG)) == [PKG]


def test_subpackages_adds_only_public_ones_with_all(fake_package):
    assert names(get_all_only(PKG, subpackages=True)) == [PKG, f"{PKG}.factory"]
