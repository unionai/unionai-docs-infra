#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Audit the generated API reference for defects a reader can see.

The generated pages are produced from SDK docstrings by `tools/api_generator`.
When a docstring carries markup the generator does not understand, or a section
shape it does not parse, the result is visible on the published page: literal
`:class:` text, a stray `::`, a code block with no language, a parameter table
whose Description column is empty. Nothing was watching for any of it, so
instances were found by eye, one page at a time.

This walks the built tree and reports each defect class with a count and
example URLs.

The headline number, empty Description columns, mixes two very different
causes, and reporting it as one figure is misleading. So the split is this
script's first job:

  parser-drop    the source docstring documents the parameter and the
                 generator dropped it. A real defect: fix the docstring's
                 section syntax (or the parser).

  undocumented   nothing in the source to render. Not a bug; a content gap,
                 and almost certainly the larger bucket.

The split needs the SDK source, so pass --source. Without it every empty
description is reported as `unclassified` rather than guessed at.

Advisory by design. It exits 0 unless --fail-on names a class explicitly, the
same posture as the markdownlint and spelling checks.

Usage:
    uv run tools/check_generated_api_docs.py
    uv run tools/check_generated_api_docs.py --source ~/repos/flyte-sdk
    uv run tools/check_generated_api_docs.py --json report.json --examples 5
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _repo_root() -> Path:
    """The content repo root, however this is invoked."""
    try:
        from _repo import get_repo_root

        return get_repo_root()
    except Exception:
        return Path.cwd()

# --- rendered-defect patterns ------------------------------------------------

ROLE_RE = re.compile(
    r":(?:py:)?(?:class|func|meth|attr|data|mod|obj|exc|ref|term|doc|option|envvar):`[^`]*`"
)
LITERAL_BLOCK_RE = re.compile(r"[^:\s]\s*::\s*$")
FENCE_RE = re.compile(r"^\s*```(?P<lang>[^\s`]*)\s*$")
ENTITY_RE = re.compile(r"&(?:gt|lt|amp|quot|#\d+);")
TABLE_HEADER_RE = re.compile(r"^\|\s*(Parameter|Property|Attribute)\s*\|\s*Type\s*\|\s*Description\s*\|\s*$")
TABLE_RULE_RE = re.compile(r"^\|[-: |]+\|$")
ROW_RE = re.compile(r"^\|(?P<cells>.*)\|\s*$")
PACKAGE_RE = re.compile(r"^\*\*Package:\*\*\s*`(?P<pkg>[\w.]+)`\s*$")
MEMBER_HEADING_RE = re.compile(r"^#{2,4}\s+(?P<name>[\w.]+)\(\)\s*$")
SECTION_HEADING_RE = re.compile(r"^#{2,4}\s+(?P<name>Returns|Raises|Yields)\s*$")
VARARG_TYPE_RE = re.compile(r"^`\*{1,2}\w*`$")

DESCRIPTIONS = {
    "rst-role": "literal reStructuredText role, e.g. `:class:`X``, rendered as text",
    "rst-literal-block": "stray `::` left by an RST literal-block marker",
    "code-block-no-language": "fenced code block with no language, so nothing highlights",
    "escaped-entity": "HTML entity rendered literally, e.g. `-&gt;` instead of `->`",
    "empty-description": "parameter/property table row with an empty Description cell",
    "vararg-in-type": "`*args` / `**kwargs` emitted in the Type column instead of a type",
    "missing-default": "source declares a default the rendered signature does not show",
    "empty-returns-raises": "a Returns/Raises/Yields heading with nothing under it",
}


class Finding:
    __slots__ = ("bucket", "detail", "kind", "line", "path", "url")

    def __init__(self, kind, path, line, url, detail, bucket=None):
        self.kind = kind
        self.path = path
        self.line = line
        self.url = url
        self.detail = detail
        self.bucket = bucket


# --- source index (for the parser-drop vs undocumented split) ----------------

DOCUMENTED_PARAM_RE = re.compile(
    r"^\s*(?:Args|Arguments|Attributes|Parameters|Other Parameters):?\s*$|^\s*:param\b",
    re.MULTILINE,
)


class SourceIndex:
    """module.QualName -> docstring, built by parsing (never importing) the SDK."""

    def __init__(self):
        self.docs: dict[str, str] = {}
        self.defaults: dict[str, set[str]] = {}

    @classmethod
    def build(cls, roots: list[Path]) -> "SourceIndex":
        idx = cls()
        for root in roots:
            for py in sorted(root.rglob("*.py")):
                rel = py.relative_to(root).with_suffix("")
                parts = list(rel.parts)
                if parts and parts[-1] == "__init__":
                    parts = parts[:-1]
                mod = ".".join(parts)
                try:
                    tree = ast.parse(py.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError):
                    continue
                idx._walk(tree, mod)
        return idx

    def _walk(self, node, prefix):
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                doc = ast.get_docstring(child, clean=False)
                if doc:
                    self.docs[qual] = doc
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.defaults[qual] = self._defaulted(child)
                else:
                    self.defaults[qual] = self._class_defaults(child)
                self._walk(child, qual)

    @staticmethod
    def _defaulted(fn) -> set[str]:
        out = set()
        a = fn.args
        pos = a.posonlyargs + a.args
        for arg, _ in zip(pos[len(pos) - len(a.defaults):], a.defaults):
            out.add(arg.arg)
        for arg, d in zip(a.kwonlyargs, a.kw_defaults):
            if d is not None:
                out.add(arg.arg)
        return out

    @staticmethod
    def _class_defaults(cls) -> set[str]:
        """Dataclass / pydantic-style fields that carry a default."""
        out = set()
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                out.add(stmt.target.id)
            elif isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        out.add(t.id)
        return out

    def lookup(self, *candidates: str) -> str | None:
        for c in candidates:
            if c in self.docs:
                return self.docs[c]
        # fall back to a unique suffix match (re-exported symbols)
        for c in candidates:
            tail = "." + c.split(".")[-1]
            hits = [v for k, v in self.docs.items() if k.endswith(tail)]
            if len(hits) == 1:
                return hits[0]
        return None

    def documents_param(self, doc: str | None, name: str) -> bool:
        if not doc:
            return False
        if not DOCUMENTED_PARAM_RE.search(doc):
            return False
        # the name must actually appear as an entry, not merely in prose
        entry = re.compile(rf"^\s*(?:\*{{0,2}}{re.escape(name)}\s*(?:\([^)]*\))?\s*:|:param\s+{re.escape(name)}\s*:)", re.MULTILINE)
        return bool(entry.search(doc))


# --- page scanning -----------------------------------------------------------


def page_url(path: Path, content_root: Path, base: str) -> str:
    rel = path.relative_to(content_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "_index":
        parts = parts[:-1]
    return f"{base}/" + "/".join(parts) + ("/" if parts else "")


def split_row(line: str) -> list[str] | None:
    m = ROW_RE.match(line)
    if not m:
        return None
    # a pipe escaped as \| belongs to a cell, not the table grid
    guarded = m.group("cells").replace(r"\|", "\x00")
    return [c.replace("\x00", "|").strip() for c in guarded.split("|")]


def scan_page(path: Path, content_root: Path, base: str, index: SourceIndex | None) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    url = page_url(path, content_root, base)
    found: list[Finding] = []

    package = None
    title = None
    fm = False
    in_fence = False
    current_symbol = None
    last_signature = ""
    fence_buf: list[str] = []
    prev_blank = True
    in_indented_block = False

    for i, line in enumerate(lines, start=1):
        if i == 1 and line.strip() == "---":
            fm = True
            continue
        if fm:
            if line.strip() == "---":
                fm = False
            elif line.startswith("title:"):
                title = line.split(":", 1)[1].strip()
            continue

        fence = FENCE_RE.match(line)
        if fence:
            if not in_fence:
                in_fence = True
                fence_buf = []
                if not fence.group("lang"):
                    found.append(Finding("code-block-no-language", path, i, url, "``` with no language"))
            else:
                in_fence = False
                last_signature = "\n".join(fence_buf)
            prev_blank = False
            continue

        # An RST literal block survives into markdown as a 4-space INDENTED
        # code block, which is exactly the language-less case: it renders as a
        # <pre> with no language, so the highlighter has nothing to work with.
        if not in_fence:
            indented = line.startswith("    ") and line.strip() and not line.lstrip().startswith(("|", "-", "*", ">"))
            if indented and prev_blank and not in_indented_block:
                in_indented_block = True
                found.append(
                    Finding("code-block-no-language", path, i, url, f"indented block: {line.strip()[:60]}")
                )
            elif not line.strip():
                pass
            elif not indented:
                in_indented_block = False

        prev_blank = not line.strip()

        if package is None:
            pm = PACKAGE_RE.match(line)
            if pm:
                package = pm.group("pkg")
                current_symbol = f"{package}.{title}" if title else None

        mh = MEMBER_HEADING_RE.match(line)
        if mh and package:
            base_sym = f"{package}.{title}" if title else package
            current_symbol = f"{base_sym}.{mh.group('name')}"

        if in_fence:
            fence_buf.append(line)
            continue

        for m in ROLE_RE.finditer(line):
            found.append(Finding("rst-role", path, i, url, m.group(0)))
        if LITERAL_BLOCK_RE.search(line):
            found.append(Finding("rst-literal-block", path, i, url, line.strip()[:90]))
        for m in ENTITY_RE.finditer(line):
            found.append(Finding("escaped-entity", path, i, url, m.group(0)))

        sh = SECTION_HEADING_RE.match(line)
        if sh:
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or lines[j].startswith("#"):
                found.append(Finding("empty-returns-raises", path, i, url, sh.group("name")))

        if TABLE_HEADER_RE.match(line):
            found += scan_table(lines, i, path, url, current_symbol, index, last_signature)

    return found


def scan_table(lines, header_lineno, path, url, symbol, index, signature="") -> list[Finding]:
    found: list[Finding] = []
    i = header_lineno  # 1-based header; lines[i] is the rule row
    if i >= len(lines) or not TABLE_RULE_RE.match(lines[i]):
        return found
    doc = index.lookup(symbol) if (index and symbol) else None
    defaults = index.defaults.get(symbol, set()) if (index and symbol) else set()

    i += 1
    while i < len(lines):
        cells = split_row(lines[i])
        if not cells or len(cells) < 3:
            break
        name_cell, type_cell, desc_cell = cells[0], cells[1], cells[2]
        name = name_cell.strip("`* ")
        lineno = i + 1

        if VARARG_TYPE_RE.match(type_cell):
            found.append(Finding("vararg-in-type", path, lineno, url, f"{name_cell} -> {type_cell}"))

        if not desc_cell:
            if index is None:
                bucket = "unclassified"
            elif index.documents_param(doc, name):
                bucket = "parser-drop"
            elif doc is None:
                bucket = "unclassified"
            else:
                bucket = "undocumented"
            found.append(Finding("empty-description", path, lineno, url, name_cell, bucket))

        # the source declares a default; the rendered signature never shows one
        if defaults and name in defaults and signature and not re.search(
            rf"^\s*{re.escape(name)}\s*(?::[^=\n]*)?=", signature, re.MULTILINE
        ):
            found.append(Finding("missing-default", path, lineno, url, f"{name} has a default in source"))
        i += 1
    return found


# --- reporting ---------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--content", default=None, help="generated API-reference dir (default: content/api-reference)")
    ap.add_argument("--content-root", default=None, help="content root, for URL derivation (default: content)")
    ap.add_argument("--source", default=None, help="flyte-sdk checkout, enabling the parser-drop vs undocumented split")
    ap.add_argument("--base-url", default="https://www.union.ai/docs/v2/union")
    ap.add_argument("--examples", type=int, default=3, help="example URLs per class (default 3)")
    ap.add_argument("--json", default=None, help="also write the full report as JSON")
    ap.add_argument(
        "--fail-on",
        default="",
        help="comma-separated defect classes that should exit non-zero (default: none, advisory)",
    )
    args = ap.parse_args()

    repo = _repo_root()
    content_root = Path(args.content_root) if args.content_root else repo / "content"
    api_dir = Path(args.content) if args.content else content_root / "api-reference"
    if not api_dir.is_dir():
        print(f"error: no generated API reference at {api_dir}", file=sys.stderr)
        return 2

    index = None
    if args.source:
        src = Path(args.source)
        roots = [src / "src"] + sorted(src.glob("plugins/*/src")) + sorted(src.glob("plugins/*/*/src"))
        roots = [r for r in roots if r.is_dir()]
        if not roots:
            print(f"error: no source trees under {src}", file=sys.stderr)
            return 2
        index = SourceIndex.build(roots)

    pages = sorted(api_dir.rglob("*.md"))
    findings: list[Finding] = []
    for p in pages:
        findings += scan_page(p, content_root, args.base_url.rstrip("/"), index)

    by_kind: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_kind[f.kind].append(f)

    print(f"Generated API reference: {len(pages)} pages under {api_dir}")
    if index is None:
        print("No --source given: empty descriptions are reported as `unclassified`.")
    print()

    width = max((len(k) for k in DESCRIPTIONS), default=20)
    for kind in sorted(DESCRIPTIONS):
        items = by_kind.get(kind, [])
        pagecount = len({f.path for f in items})
        print(f"{kind:{width}}  {len(items):6d}  on {pagecount:4d} page(s)")
        if not items:
            continue
        print(f"{'':{width}}  {DESCRIPTIONS[kind]}")
        if kind == "empty-description":
            buckets: dict[str, list[Finding]] = defaultdict(list)
            for f in items:
                buckets[f.bucket or "unclassified"].append(f)
            for b in ("parser-drop", "undocumented", "unclassified"):
                if b in buckets:
                    bp = len({f.path for f in buckets[b]})
                    print(f"{'':{width}}    {b:14s} {len(buckets[b]):6d}  on {bp:4d} page(s)")
            for b in ("parser-drop", "undocumented", "unclassified"):
                for f in buckets.get(b, [])[: args.examples]:
                    print(f"{'':{width}}      [{b}] {f.url}  {f.detail}")
        else:
            seen = set()
            shown = 0
            for f in items:
                if f.url in seen:
                    continue
                seen.add(f.url)
                print(f"{'':{width}}      {f.url}  {f.detail[:70]}")
                shown += 1
                if shown >= args.examples:
                    break
        print()

    print(f"total findings: {len(findings)}")

    if args.json:
        payload = {
            "pages": len(pages),
            "content": str(api_dir),
            "source": args.source,
            "classes": {
                k: {
                    "count": len(v),
                    "pages": len({str(f.path) for f in v}),
                    "buckets": (
                        {b: sum(1 for f in v if (f.bucket or "unclassified") == b) for b in
                         sorted({f.bucket or "unclassified" for f in v})}
                        if k == "empty-description" else None
                    ),
                    "examples": [
                        {"url": f.url, "line": f.line, "detail": f.detail, "bucket": f.bucket}
                        for f in v[:25]
                    ],
                }
                for k, v in sorted(by_kind.items())
            },
        }
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    fail = {k.strip() for k in args.fail_on.split(",") if k.strip()}
    if fail & {k for k, v in by_kind.items() if v}:
        offending = sorted(fail & {k for k, v in by_kind.items() if v})
        print(f"\nFAIL: {', '.join(offending)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
