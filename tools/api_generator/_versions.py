"""Shared version helpers: PyPI lookups + Hugo frontmatter version extraction.

Factored out of ``check_versions.py`` so the docs-version manifest resolver
(``manifest.py``) can reuse them without duplication. Both are pure functions.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

from packaging.version import Version


def extract_frontmatter_version(version_file: Path) -> str | None:
    """Extract the ``version:`` field from a page's Hugo YAML frontmatter."""
    if not version_file.exists():
        return None
    text = version_file.read_text()
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


def get_pypi_latest(package: str) -> str | None:
    """Return the latest stable (non-prerelease) version of ``package`` on PyPI."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  Warning: failed to query PyPI for {package}: {e}", file=sys.stderr)
        return None

    versions = []
    for ver_str, files in data.get("releases", {}).items():
        if not files:
            continue
        if all(f.get("yanked", False) for f in files):
            continue
        try:
            v = Version(ver_str)
            if not v.is_prerelease:
                versions.append(v)
        except Exception:
            continue

    if not versions:
        return None
    return str(max(versions))
