#!/usr/bin/env python3
"""Resolve every CSS custom property in base.css for each theme x variant context.

Phase 1 of the docs reskin (DOC-1308) is a pure refactor: it introduces a
scale-keyed primitive token layer and re-expresses the existing component tokens
on top of it, without changing a single resolved value.

This script is the proof. Run it before and after the refactor and diff the
output; an empty diff is the acceptance criterion.

Usage:  uv run python tools/token_resolve.py [path/to/base.css]
"""
import re
import sys
from pathlib import Path

CSS = Path(sys.argv[1] if len(sys.argv) > 1 else "static/css/base.css")

# Selector -> (specificity, applies-to-context predicate).
# Specificity is the plain CSS a/b/c count; higher wins on conflict.
CONTEXTS = {
    "union-light": [":root"],
    "flyte-light": [":root", ":root .variant-flyte"],
    "union-dark": [":root", '[data-theme="dark"]'],
    "flyte-dark": [
        ":root",
        ":root .variant-flyte",
        '[data-theme="dark"]',
        '[data-theme="dark"] .variant-flyte',
    ],
}

SPECIFICITY = {
    ":root": (0, 1, 0),
    ":root .variant-flyte": (0, 2, 0),
    '[data-theme="dark"]': (0, 1, 0),
    '[data-theme="dark"] .variant-flyte': (0, 2, 0),
}

comment = re.compile(r"/\*.*?\*/", re.S)
var_ref = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)")


def blocks(src):
    """Yield (selector, {prop: value}) for each top-level rule that sets custom props."""
    src = comment.sub("", src)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", src):
        # A selector runs from the end of the previous rule; drop any at-rules
        # (@import/@charset) that precede the first one.
        sel = " ".join(m.group(1).split("}")[-1].split(";")[-1].split())
        body = m.group(2)
        props = {}
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            name, _, val = decl.partition(":")
            name = name.strip()
            if name.startswith("--"):
                props[name] = val.strip()
        if props:
            yield sel, props


def build(src):
    """selector -> props, keeping later duplicates (source order = later wins)."""
    out = {}
    for sel, props in blocks(src):
        out.setdefault(sel, {}).update(props)
    return out


def cascade(by_sel, selectors):
    """Merge the applicable selectors by specificity, then source order."""
    merged = {}
    ordered = sorted(
        (s for s in selectors if s in by_sel),
        key=lambda s: (SPECIFICITY.get(s, (0, 0, 0)), selectors.index(s)),
    )
    for sel in ordered:
        merged.update(by_sel[sel])
    return merged


def resolve(name, env, seen=None):
    """Recursively resolve a token to a literal, honouring var() fallbacks."""
    seen = seen or set()
    if name in seen:
        return "<cycle>"
    if name not in env:
        return "<unset>"
    seen = seen | {name}
    val = env[name]

    def sub(m):
        ref, fallback = m.group(1), m.group(2)
        r = resolve(ref, env, seen)
        if r in ("<unset>", "<cycle>") and fallback is not None:
            return fallback.strip()
        return r

    prev = None
    while prev != val and "var(" in val:
        prev = val
        val = var_ref.sub(sub, val)
    return " ".join(val.replace("!important", "").split())


def main():
    src = CSS.read_text()
    by_sel = build(src)
    names = sorted({n for props in by_sel.values() for n in props})
    for ctx, selectors in CONTEXTS.items():
        env = cascade(by_sel, selectors)
        print(f"=== {ctx} ===")
        for n in names:
            print(f"{n} = {resolve(n, env)}")
        print()


if __name__ == "__main__":
    main()
