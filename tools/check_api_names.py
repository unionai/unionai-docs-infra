#!/usr/bin/env python3
"""Fail when a hand-written page names a Python API that does not exist.

WHY

A backticked name such as `flyte.io.DataFrame` tells the reader the thing
exists and that copying it will work. Nothing checked that. The link checker
validates links and check-api-docs keeps the generated reference current, but a
hand-written page could say `flyte.io.dataframe` or `flyte.File` indefinitely:
the autolinker leaves a name it does not know unlinked, without complaint, and
no build step fails.

HOW A NAME IS RESOLVED

  1. In the linkmap, by the rules assets/js/inline-code-linker.js uses: an exact
     key, ignoring a trailing `()` and a leading `@`, or `Class.member` whenever
     `Class` is a documented identifier. A documented package also counts. A
     name that resolves here is one the reader sees linked.
  2. Otherwise, in the released SDK the reference was generated from (the
     `version:` in the front matter of each [[sdks]] version_file in
     api-packages.toml), by importing it. A name that resolves here is real but
     missing from the reference, so it is reported, not failed.
  3. Otherwise it fails, unless .api-names-exclude matches it.

Only names under a root the linkmap documents (`flyte`, `flyteplugins`) are in
scope, so `env.task` and `pd.DataFrame` are left alone. A plugin name that
misses the linkmap is reported as not checked, because the plugin is not
installed here. A forced link, `[[X]]` or `[[X|label]]`, is checked whatever its
root, because the author asked for a link and would otherwise get none.

V1 SDK ON A V2 PAGE

In the v2 docs, `{{< key kit >}}`, `kit_as`, `kit_import`, `kit_name` and
`kit_remote` render the Flyte 1 SDK's names (`union`, `flytekit`, `fl`). They
are how v1 content gets copied into v2 pages: the Azure, AWS and Google
secret-manager pages all render `import union`. A kit key on a v2 page fails;
this rule reads the whole page, code blocks included. Backticked `flytekit.*`
and `union.*` names are only reported, because most are deliberate Flyte 1
comparisons ("`flytekit.map_task` becomes ...").

SCOPE

Hand-written prose outside fenced code blocks. Excluded: api-reference/
(generated), __docs_builder__/, and release-notes/, which are historical: a name
that was right in May is not wrong because it was renamed in June.

EXCLUSIONS

.api-names-exclude in the docs repository, one regex per line matched against
"page:name", the same convention as .link-checker-exclude, so a docs pull
request can add its own. It is for names that are not Python at all, such as
OpenTelemetry attribute keys (`flyte.run_name`) and logger names
(`flyte.user`), and for findings that predate this check. An entry that no
longer matches anything is reported so that it can be removed.

EXIT STATUS

  0  every name resolves, or is excluded
  1  at least one name does not exist
  2  the check could not run: no content, no linkmap, or the SDK could not be
     installed. A check that could not run never reports a pass.

Usage:
    check_api_names.py [content-dir] [--linkmap DIR] [--exclude-file FILE]
                       [--api-packages FILE] [--version v2]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

DEFAULT_EXCLUDES = ("api-reference", "__docs_builder__", "release-notes")
FENCE = re.compile(r"^\s*(```|~~~)")
SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
DOTTED = re.compile(r"@?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?:\(\))?")
OPT_OUT = re.compile(r"\{\{(.+)\}\}")
FORCED = re.compile(r"\[\[(.+?)\]\]")
KIT_KEY = re.compile(r"\{\{[<%]\s*key\s+(kit\w*)\s*[>%]\}\}")
V1_ROOTS = ("flytekit", "union")
NOT_V1 = {"union.ai"}  # the domain, not the v1 package

# (sdk root, names) -> {name: exists}, or None if the SDK could not be loaded.
SdkResolver = Callable[[str, set], "dict | None"]


def bare(name: str) -> str:
    name = name[:-2] if name.endswith("()") else name
    return name[1:] if name.startswith("@") else name


@dataclass
class Linkmap:
    identifiers: dict = field(default_factory=dict)
    methods: dict = field(default_factory=dict)
    packages: dict = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path) -> "Linkmap":
        lm = cls()
        for f in sorted(directory.glob("*-linkmap.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            lm.identifiers.update(data.get("identifiers") or {})
            lm.methods.update(data.get("methods") or {})
            lm.packages.update(data.get("packages") or {})
        return lm

    @property
    def empty(self) -> bool:
        return not (self.identifiers or self.methods or self.packages)

    @property
    def roots(self) -> set[str]:
        return {k.split(".")[0] for k in self.packages if "." in k or k.isidentifier()}

    def _last_segments(self, table: dict) -> set[str]:
        return {k.rsplit(".", 1)[-1] for k in table}

    def resolves(self, name: str) -> bool:
        """Plain inline code, as the autolinker's regular matching sees it."""
        t = bare(name)
        if t in self.identifiers or t in self.methods or t in self.packages:
            return True
        cls, _, _member = t.rpartition(".")
        return bool(cls) and (cls in self.identifiers
                              or cls in self._last_segments(self.identifiers))

    def resolves_forced(self, target: str) -> bool:
        """`[[target]]`: direct key, then `Class.method` by class name, then last segment."""
        if target in self.identifiers or target in self.methods:
            return True
        t = bare(target)
        m = re.fullmatch(r"([^.]+)\.(.+)", t)
        if m and m.group(1) in self._last_segments(self.identifiers):
            return True
        return t in self._last_segments(self.methods) or t in self._last_segments(self.identifiers)


def frontmatter_end(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for i, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            return i
    return 0


def spans(text: str):
    """(line number, inline code text) for prose outside front matter and fences."""
    lines = text.splitlines()
    start = frontmatter_end(lines)
    fence = None
    for n, line in enumerate(lines, 1):
        if n <= start:
            continue
        m = FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1) == fence:
                fence = None
            continue
        if fence is None:
            for s in SPAN.finditer(line):
                yield n, s.group(1).strip()


def version_of(page: Path) -> str | None:
    """The `version:` front matter field; same rule as api_generator/_versions.py."""
    if not page.is_file():
        return None
    m = re.match(r"^---\s*\n(.*?)\n---", page.read_text(encoding="utf-8"), re.DOTALL)
    for line in (m.group(1).splitlines() if m else []):
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def sdk_specs(api_packages: Path, repo_root: Path) -> dict[str, str]:
    """{import root: pip spec pinned to the version the reference was built from}."""
    if not api_packages.is_file():
        return {}
    try:
        import tomllib
        config = tomllib.loads(api_packages.read_text(encoding="utf-8"))
    except ModuleNotFoundError:
        import toml
        config = toml.loads(api_packages.read_text(encoding="utf-8"))
    specs = {}
    for sdk in config.get("sdks", []):
        version = version_of(repo_root / sdk["version_file"])
        root = sdk.get("parser_package", sdk["package"]).split(".")[0]
        if version:
            specs[root] = f"{sdk.get('install', sdk['package'])}=={version}"
    return specs


RESOLVER = r"""
import importlib, json, sys
def exists(name):
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:
            continue
        for p in parts[i:]:
            if not hasattr(obj, p):
                return False
            obj = getattr(obj, p)
        return True
    return False
names = json.load(sys.stdin)
print("@@RESULT@@" + json.dumps({n: exists(n) for n in names}))
"""


def installed_sdk_resolver(specs: dict[str, str]) -> SdkResolver:
    """Import names in a throwaway environment holding the pinned SDK."""
    python = f"{sys.version_info[0]}.{sys.version_info[1]}"

    def resolve(root: str, names: set) -> dict | None:
        spec = specs.get(root)
        if not spec:
            return None
        try:
            p = subprocess.run(
                ["uv", "run", "--no-project", "--quiet", "--python", python,
                 "--with", spec, "python", "-c", RESOLVER],
                input=json.dumps(sorted(names)), capture_output=True, text=True,
                timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"check-api-names: could not install {spec}: {e}", file=sys.stderr)
            return None
        if p.returncode or "@@RESULT@@" not in p.stdout:
            print(f"check-api-names: could not install {spec}:\n{p.stderr[-2000:]}",
                  file=sys.stderr)
            return None
        return json.loads(p.stdout.rsplit("@@RESULT@@", 1)[1])

    return resolve


@dataclass
class Exclusions:
    patterns: list = field(default_factory=list)
    used: set = field(default_factory=set)

    @classmethod
    def load(cls, path: Path | None) -> "Exclusions":
        ex = cls()
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    ex.patterns.append((line, re.compile(line)))
        return ex

    def match(self, page: str, name: str) -> bool:
        for raw, rx in self.patterns:
            if rx.search(f"{page}:{name}"):
                self.used.add(raw)
                return True
        return False

    @property
    def stale(self) -> list[str]:
        return [raw for raw, _ in self.patterns if raw not in self.used]


def pages(content: Path):
    for path in sorted(content.rglob("*.md")):
        rel = path.relative_to(content)
        if rel.parts and rel.parts[0] in DEFAULT_EXCLUDES:
            continue
        yield rel.as_posix(), path.read_text(encoding="utf-8")


def show(items, limit=15, fmt=lambda x: x):
    for x in items[:limit]:
        print(f"  {fmt(x)}")
    if len(items) > limit:
        print(f"  ... and {len(items) - limit} more")


def run(content: Path, linkmap_dir: Path, exclude_file: Path | None, version: str,
        resolve_sdk: SdkResolver, sdk_roots: set) -> int:
    if not content.is_dir():
        print(f"check-api-names: no such directory: {content}", file=sys.stderr)
        return 2
    lm = Linkmap.load(linkmap_dir) if linkmap_dir.is_dir() else Linkmap()
    if lm.empty:
        print(f"check-api-names: no linkmap in {linkmap_dir}; nothing to resolve against",
              file=sys.stderr)
        return 2
    excl = Exclusions.load(exclude_file)
    roots = lm.roots

    missing, forced_missing, kit_keys, v1_names = [], [], [], []
    pending: dict[str, list] = {}     # sdk root -> [(page, line, name)]
    plugins_unchecked, linked, excluded, n_pages = [], 0, 0, 0

    for page, text in pages(content):
        n_pages += 1
        if version == "v2":
            for n, line in enumerate(text.splitlines(), 1):
                for m in KIT_KEY.finditer(line):
                    finding = (page, n, m.group(0))
                    if excl.match(page, m.group(0)):
                        excluded += 1
                    else:
                        kit_keys.append(finding)
        for n, s in spans(text):
            if OPT_OUT.fullmatch(s):
                continue
            forced = FORCED.fullmatch(s)
            if forced:
                # In a Markdown table the pipe is written `\|`; the reader's page has `|`.
                target = forced.group(1).replace("\\|", "|").split("|", 1)[0].strip()
                if lm.resolves_forced(target):
                    linked += 1
                elif excl.match(page, target):
                    excluded += 1
                else:
                    forced_missing.append((page, n, target))
                continue
            if not DOTTED.fullmatch(s):
                continue
            root = bare(s).split(".")[0]
            if root in V1_ROOTS and bare(s) not in NOT_V1 and root not in roots:
                v1_names.append((page, n, s))
                continue
            if root not in roots:
                continue
            if lm.resolves(s):
                linked += 1
            else:
                pending.setdefault(root, []).append((page, n, s))

    unlinked_real, could_not_run = [], []
    for root, found in pending.items():
        names = {bare(s) for _, _, s in found}
        result = resolve_sdk(root, names)
        for page, n, s in found:
            if result is not None and result.get(bare(s)):
                unlinked_real.append((page, n, s))
            elif excl.match(page, s):
                excluded += 1
            elif result is None and root in sdk_roots:
                could_not_run.append((page, n, s))
            elif result is None:
                plugins_unchecked.append((page, n, s))
            else:
                missing.append((page, n, s))

    checked = linked + len(unlinked_real) + len(missing) + len(forced_missing) + excluded
    print(f"check-api-names: {n_pages} pages, {checked} API names "
          f"({linked} linked, {len(unlinked_real)} real but unlinked, {excluded} excluded)")

    def loc(x):
        return f"{x[0]}:{x[1]}  {x[2]}"

    if unlinked_real:
        print(f"\nNOTE: {len(unlinked_real)} name(s) exist in the released SDK but are not in the")
        print("      API reference, so readers see them without a link.")
        show(unlinked_real, fmt=loc)
    if plugins_unchecked:
        print(f"\nNOTE: {len(plugins_unchecked)} plugin name(s) not checked: they are not in the")
        print("      linkmap, and the plugin is not installed here.")
        show(plugins_unchecked, fmt=loc)
    if v1_names:
        print(f"\nNOTE: {len(v1_names)} Flyte 1 name(s). Fine in a comparison with Flyte 1;")
        print("      wrong anywhere the page tells the reader to use them.")
        show(v1_names, fmt=loc)
    if excl.stale:
        print(f"\nNOTE: {len(excl.stale)} entr(y/ies) in {exclude_file} match nothing and can be removed:")
        show(excl.stale)

    if could_not_run:
        print(f"\nCOULD NOT CHECK: {len(could_not_run)} name(s) are not in the linkmap, and the SDK")
        print("       could not be installed to look them up. This is not a pass.")
        show(could_not_run, fmt=loc)
    if forced_missing:
        print(f"\nFATAL: {len(forced_missing)} forced link(s) [[...]] have no target in the linkmap,")
        print("       so they render without a link.")
        show(forced_missing, limit=50, fmt=loc)
    if kit_keys:
        print(f"\nFATAL: {len(kit_keys)} {{{{< key kit... >}}}} shortcode(s) on v2 pages. These render the")
        print("       Flyte 1 SDK's names (`union`, `flytekit`); v2 code uses `flyte`.")
        show(kit_keys, limit=50, fmt=loc)
    if missing:
        print(f"\nFATAL: {len(missing)} API name(s) do not exist, in the API reference or the SDK.")
        print("       Correct the name. If it is not a Python name at all, such as a log")
        print(f"       attribute, a logger or a hostname, add a line to {exclude_file or '.api-names-exclude'}.")
        show(missing, limit=50, fmt=loc)

    if could_not_run:
        return 2
    if missing or forced_missing or kit_keys:
        return 1
    print("check-api-names: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content", nargs="?", default="content", type=Path)
    ap.add_argument("--linkmap", default="linkmap", type=Path)
    ap.add_argument("--exclude-file", type=Path)
    ap.add_argument("--api-packages", default="api-packages.toml", type=Path)
    ap.add_argument("--version", default=os.environ.get("VERSION") or "v2")
    args = ap.parse_args()

    specs = sdk_specs(args.api_packages, args.api_packages.resolve().parent)
    return run(args.content, args.linkmap, args.exclude_file, args.version,
               installed_sdk_resolver(specs), set(specs))


if __name__ == "__main__":
    sys.exit(main())
