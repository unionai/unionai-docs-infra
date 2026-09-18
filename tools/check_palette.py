#!/usr/bin/env python3
"""Assert the resolved token values match the Union V2 spec (DOC-1313).

Phase 2a is a deliberate visual change, so pixel-identity is not the criterion.
The criterion is that every token resolves to the value the Figma spec gives, in
all four union/flyte x light/dark contexts.

Reads the same resolver used for phase 1 so both phases agree on cascade rules.

Usage:  uv run python tools/check_palette.py
Source: runs/2026-07-30-docsy-reskin-docs-site-TOKENS.md in the docsy repo.
"""
import subprocess
import sys

# Expected values per context, from the Figma extraction.
# (token, union-light, union-dark, flyte-light, flyte-dark)
EXPECTED = [
    # surfaces — variant-invariant, mode-varying
    ("--bg",        "#ffffff", "#0e0e11", "#ffffff", "#0e0e11"),
    ("--bg-1",      "#f7f7f5", "#09090b", "#f7f7f5", "#09090b"),
    ("--bg-2",      "#f2f2f0", "#131316", "#f2f2f0", "#131316"),
    ("--bg-3",      "#eaeae7", "#1a1a1f", "#eaeae7", "#1a1a1f"),
    ("--bg-4",      "#dededa", "#232329", "#dededa", "#232329"),
    # text
    ("--text",      "#1c1a15", "#f7f7f8", "#1c1a15", "#f7f7f8"),
    ("--text-1",    "#2a2822", "#d7d7dc", "#2a2822", "#d7d7dc"),
    ("--text-2",    "#5c584f", "#9e9ea7", "#5c584f", "#9e9ea7"),
    ("--text-3",    "#8f8a80", "#6f6f79", "#8f8a80", "#6f6f79"),
    # lines — alpha in both modes
    ("--border",    "#1410081a", "#ffffff14", "#1410081a", "#ffffff14"),
    ("--border-2",  "#1410082e", "#ffffff21", "#1410082e", "#ffffff21"),
    ("--divider",   "#14100814", "#ffffff0a", "#14100814", "#ffffff0a"),
    # status hues
    ("--green",     "#15803d", "#4ade80", "#15803d", "#4ade80"),
    ("--blue",      "#1d4ed8", "#60a5fa", "#1d4ed8", "#60a5fa"),
    ("--orange",    "#c2410c", "#fb923c", "#c2410c", "#fb923c"),
    ("--red",       "#b91c1c", "#f87171", "#b91c1c", "#f87171"),
    # semantic tints
    ("--success-soft",   "#15803d0f", "#4ade8012", "#15803d0f", "#4ade8012"),
    ("--success-border", "#15803d59", "#4ade8059", "#15803d59", "#4ade8059"),
    ("--info-soft",      "#1d4ed80f", "#60a5fa14", "#1d4ed80f", "#60a5fa14"),
    ("--info-bar",       "#1d4ed873", "#60a5fa8c", "#1d4ed873", "#60a5fa8c"),
    ("--info-border",    "#1d4ed859", "#60a5fa66", "#1d4ed859", "#60a5fa66"),
    ("--warning-soft",   "#c2410c0f", "#fb923c12", "#c2410c0f", "#fb923c12"),
    ("--warning-bar",    "#c2410c73", "#fb923c8c", "#c2410c73", "#fb923c8c"),
    ("--warning-border", "#c2410c59", "#fb923c59", "#c2410c59", "#fb923c59"),
    ("--danger-soft",    "#b91c1c0f", "#f8717112", "#b91c1c0f", "#f8717112"),
    ("--danger-bar",     "#b91c1c73", "#f871718c", "#b91c1c73", "#f871718c"),
    ("--danger-border",  "#b91c1c59", "#f8717159", "#b91c1c59", "#f8717159"),
    # code surface — dark in both modes
    ("--code-surface",      "#16181d", "#131316", "#16181d", "#131316"),
    ("--code-surface-text", "#d6d9de", "#d7d7dc", "#d6d9de", "#d7d7dc"),
    # accent — the one axis that varies by variant, and darkens in light mode
    ("--accent",    "#b45309", "#f5a623", "#7c3aed", "#c084fc"),
    ("--on-accent", "#ffffff", "#131108", "#ffffff", "#131108"),
    # brand marks — invariant across BOTH axes (the toggle shows both at once)
    # Brand seed, per Figma `Union/source/seed` — the brand MARK colour, distinct
    # from --dacc. Invariant across both axes: the toggle shows both products.
    ("--brand-union", "#FCB51D", "#FCB51D", "#FCB51D", "#FCB51D"),
    # Foreground on brand surfaces. White on gold is 1.78:1 and fails; this is 10.59:1.
    ("--on-brand",    "#131108", "#131108", "#131108", "#131108"),
    ("--brand-flyte", "#c084fc", "#c084fc", "#c084fc", "#c084fc"),
    # component aliases that must track the primitives above
    ("--bg-color",     "#ffffff", "#0e0e11", "#ffffff", "#0e0e11"),
    ("--text-color",   "#1c1a15", "#f7f7f8", "#1c1a15", "#f7f7f8"),
    ("--sidebar-bg",   "#f7f7f5", "#09090b", "#f7f7f5", "#09090b"),
    ("--border-color", "#1410081a", "#ffffff14", "#1410081a", "#ffffff14"),
    ("--separator",    "#1410082e", "#ffffff21", "#1410082e", "#ffffff21"),
    ("--code-bg",      "#f2f2f0", "#131316", "#f2f2f0", "#131316"),
    ("--pre-bg",       "#16181d", "#131316", "#16181d", "#131316"),
    ("--union-color",  "#b45309", "#f5a623", "#7c3aed", "#c084fc"),
    # Accent DERIVATIVES — these are the ones the original resolver could not
    # see. They are computed on the declaring element, so if they live only on
    # :root the Flyte variant inherits the Union value and links render orange.
    ("--accent-dark",       "#b45309", "#f5a623", "#7c3aed", "#c084fc"),
    ("--accent-search",     "#b45309", "#f5a623", "#7c3aed", "#c084fc"),
    ("--union-color-dark",  "#b45309", "#f5a623", "#7c3aed", "#c084fc"),
    ("--docsearch-primary-color", "#b45309", "#f5a623", "#7c3aed", "#c084fc"),
]

CONTEXTS = ["union-light", "union-dark", "flyte-light", "flyte-dark"]


def resolved():
    out = subprocess.run(
        [sys.executable, "tools/token_resolve.py"], capture_output=True, text=True
    ).stdout
    ctx, table = None, {c: {} for c in CONTEXTS}
    for line in out.splitlines():
        if line.startswith("=== "):
            ctx = line.strip("= ").strip()
        elif " = " in line and ctx in table:
            k, _, v = line.partition(" = ")
            table[ctx][k.strip()] = v.strip()
    return table


def main():
    table = resolved()
    fails, unset = [], []
    for row in EXPECTED:
        token, *want = row
        for ctx, expected in zip(CONTEXTS, want):
            got = table[ctx].get(token, "<missing>")
            if got != expected:
                fails.append((token, ctx, expected, got))
    for ctx in CONTEXTS:
        for k, v in table[ctx].items():
            if v in ("<unset>", "<cycle>"):
                unset.append((k, ctx, v))

    checks = len(EXPECTED) * len(CONTEXTS)
    print(f"palette checks: {checks - len(fails)}/{checks} pass")
    if fails:
        print("\nMISMATCHES:")
        for t, c, e, g in fails:
            print(f"  {t:22} {c:12} expected {e:12} got {g}")
    if unset:
        print("\nUNRESOLVED:")
        for t, c, v in unset:
            print(f"  {t:22} {c:12} {v}")
    return 1 if (fails or unset) else 0


if __name__ == "__main__":
    sys.exit(main())
