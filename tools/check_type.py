#!/usr/bin/env python3
"""Assert the type scale in base.css against the Figma `docs/*` text styles.

Companion to check_palette.py (DOC-1313). Same idea, different quadrant: the
palette check exists because a token can resolve differently per variant/mode;
this one exists because a type scale is a set of arithmetic relationships
(size -> line-height ratio, percent tracking -> em) that are easy to transcribe
wrongly and impossible to eyeball afterwards.

Figma gives px and percent. We ship rem and em. Every conversion below is
recomputed from the Figma number rather than trusting what got typed into the
CSS, so a fat-fingered ratio fails here instead of shipping.

    python3 tools/check_type.py
"""

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "base.css"
ROOT_PX = 16.0

# (token, figma px) — from the Figma docs/* styles, plus H5/H6 which the
# gallery renders but does not name.
SIZES = {
    "--type-h1": 34,
    "--type-h2": 21,
    "--type-h3": 16.5,
    "--type-h4": 14.5,
    "--type-h5": 13,
    "--type-h6": 11,
    "--type-lead": 16.5,
    "--type-body": 14.5,
    "--type-small": 13.5,
    "--type-crumb": 12.5,
    "--type-code": 12.5,
    "--type-code-tab": 11,
}

# (token, figma line-height px, figma size px) — ratio must match to 0.01
LINE_HEIGHTS = {
    "--lh-h1": (41, 34),
    "--lh-h2": (27, 21),
    "--lh-h3": (22, 16.5),
    "--lh-h4": (20, 14.5),
    "--lh-lead": (29, 16.5),
    "--lh-body": (26, 14.5),
    "--lh-small": (23, 13.5),
    "--lh-crumb": (16, 12.5),
    "--lh-code": (22, 12.5),
    "--lh-code-tab": (14, 11),
}

# (token, figma percent) — Figma's tracking is a percentage of font size, which
# is exactly what em means. This is the conversion that was nearly got wrong.
TRACKING = {
    "--track-h1": -2,
    "--track-h2": -1,
    "--track-caps": 6,
}


def declarations(text):
    """Every `--token: value;` in the file, last declaration winning."""
    out = {}
    for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text):
        out[name] = value.strip()
    return out


def main():
    text = CSS.read_text()
    decl = declarations(text)
    failures = []
    checked = 0

    for token, px in SIZES.items():
        checked += 1
        raw = decl.get(token)
        if raw is None:
            failures.append(f"{token}: not declared")
            continue
        m = re.fullmatch(r"([\d.]+)rem", raw)
        if not m:
            failures.append(f"{token}: expected a rem value, got {raw!r}")
            continue
        got_px = float(m.group(1)) * ROOT_PX
        if abs(got_px - px) > 0.001:
            failures.append(
                f"{token}: {raw} = {got_px}px, Figma says {px}px "
                f"(want {px / ROOT_PX}rem)"
            )

    for token, (lh, size) in LINE_HEIGHTS.items():
        checked += 1
        raw = decl.get(token)
        if raw is None:
            failures.append(f"{token}: not declared")
            continue
        if raw.endswith(("px", "rem", "em", "%")):
            failures.append(
                f"{token}: {raw!r} carries a unit. Line heights must be unitless "
                f"so they scale with the reader's font size."
            )
            continue
        want = lh / size
        if abs(float(raw) - want) > 0.01:
            failures.append(
                f"{token}: {raw}, but {lh}/{size} = {want:.4f} (want {want:.2f})"
            )

    for token, pct in TRACKING.items():
        checked += 1
        raw = decl.get(token)
        if raw is None:
            failures.append(f"{token}: not declared")
            continue
        m = re.fullmatch(r"(-?[\d.]+)em", raw)
        if not m:
            failures.append(
                f"{token}: expected em, got {raw!r}. Figma tracking is a percentage "
                f"of font size; copying its px rendering hard-codes one size."
            )
            continue
        want = pct / 100
        if abs(float(m.group(1)) - want) > 0.0001:
            failures.append(f"{token}: {raw}, Figma says {pct}% = {want}em")

    # A stack that names a font we never request renders as the fallback and
    # nobody notices until a designer opens the page.
    baseof = CSS.parent.parent.parent / "layouts" / "_default" / "baseof.html"
    link = baseof.read_text()
    for family, weights in (("Inter", ("400", "600", "700")), ("Roboto+Mono", ("400",))):
        checked += 1
        m = re.search(rf"family={re.escape(family)}:wght@([\d;]+)", link)
        if not m:
            failures.append(f"{family}: not requested in baseof.html")
            continue
        have = set(m.group(1).split(";"))
        missing = [w for w in weights if w not in have]
        if missing:
            failures.append(
                f"{family}: weight(s) {', '.join(missing)} used in CSS but not loaded"
            )

    checked += 1
    if "Instrument Sans" in text or "Open Sans" in text:
        failures.append("a retired font family is still named in base.css")

    print(f"  {checked - len(failures)}/{checked} pass")
    if failures:
        print("--- failures ---")
        for f in failures:
            print(f"  {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
