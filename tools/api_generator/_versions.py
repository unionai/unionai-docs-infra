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


def pypi_version_exists(package: str, version: str) -> bool | None:
    """Whether ``version`` is a published (non-yanked) release of ``package`` on PyPI.

    Returns True/False, or None if PyPI can't be reached (so the caller can warn
    rather than block on a transient outage). Compares with version normalization
    (e.g. ``2.5.12`` matches PyPI's ``2.5.12``) via ``packaging.Version``.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            releases = json.loads(resp.read()).get("releases", {})
    except Exception as e:
        print(f"  Warning: failed to query PyPI for {package}: {e}", file=sys.stderr)
        return None

    def _live(files) -> bool:
        return bool(files) and not all(f.get("yanked", False) for f in files)

    if version in releases:
        return _live(releases[version])
    try:
        target = Version(version)
    except Exception:
        return False
    for ver_str, files in releases.items():
        try:
            if Version(ver_str) == target and _live(files):
                return True
        except Exception:
            continue
    return False


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
