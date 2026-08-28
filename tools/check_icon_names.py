#!/usr/bin/env python3
"""Fail on an `icon="..."` value that does not exist in the set its shortcode uses.

Why (DOC-1444): our shortcodes take an `icon=` parameter, but they do NOT all
resolve it the same way, and the two vocabularies are mutually unintelligible:

  * link-card, note, warning, code, download, icon  ->  <sl-icon name="...">,
    which Shoelace resolves against BOOTSTRAP ICONS on the CDN. An unknown name
    fetches 404 and renders an EMPTY slot.
  * dropdown                                        ->  transform.Emojify(":x:"),
    i.e. a GEMOJI alias. An unknown alias renders the literal text ":x:".

Both fail silently. Nothing errors, the build stays green, and no other check
sees it: the sl-icon is decorative and aria-hidden, and a missing icon is not a
broken link, so check_internal_links.py is structurally blind to it. That silence
let four dead Bootstrap names accumulate across nine call sites on two version
lines. They are Lucide names (`git-branch`, `package-2`, `settings`, `zap`),
i.e. written against the wrong icon vocabulary. The worst sat in
contributing-docs/shortcodes.md, the page that TEACHES the shortcode, so the bad
name propagated by being copied.

Checking `icon=` blindly across all shortcodes is exactly the mistake this file
exists to prevent: it flags `dropdown`'s perfectly valid `:bento:` and
`:control_knobs:` as dead Bootstrap names, and "fixing" those breaks working
emoji. So the check is shortcode-aware, and the shortcode->vocabulary mapping is
derived from the layouts rather than hardcoded, so a new shortcode cannot quietly
opt out.

Empty (`icon=""`) is allowed: every consumer guards with `with`/`if`, so an empty
value emits no icon at all. It is redundant, not broken.

Both name lists are vendored per pinned version rather than fetched, so the check
is deterministic and works offline; a network hiccup can never turn it green.

Frontmatter `icon:` is checked too, against Bootstrap Icons. It reaches the same
<sl-icon> and fails the same silent way, and the API generator now writes one on
every page it emits (DOC-1508), so an unchecked bad name would be a blank slot on
hundreds of pages rather than one.

Usage:
  check_icon_names.py [content-dir ...]     (default: content)
  check_icon_names.py --update              refresh the vendored lists
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "icon_sets"
LAYOUTS = HERE.parent / "layouts"
BASEOF = LAYOUTS / "_default" / "baseof.html"
SHORTCODES = LAYOUTS / "shortcodes"

AUTOLOADER = re.compile(r"@shoelace-style/shoelace@(\d+\.\d+\.\d+)/cdn/shoelace-autoloader\.js")
ANY_SHOELACE = re.compile(r"@shoelace-style/shoelace@(\d+\.\d+\.\d+)/")

SHOELACE_CDN = "https://data.jsdelivr.com/v1/packages/npm/@shoelace-style/shoelace@{v}?structure=flat"
GEMOJI_API = "https://api.github.com/emojis"


def pinned_version() -> str | None:
    if not BASEOF.is_file():
        return None
    m = AUTOLOADER.search(BASEOF.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def classify_shortcodes() -> tuple[set[str], set[str], set[str]]:
    """Derive icon vocabulary per shortcode from the layouts themselves."""
    sl, emoji, unknown = set(), set(), set()
    for path in sorted(SHORTCODES.glob("*.html")):
        src = path.read_text(encoding="utf-8")
        if '.Get "icon"' not in src and ".Get \"icon\"" not in src:
            continue
        name = path.stem
        if "sl-icon" in src:
            sl.add(name)
        elif "Emojify" in src:
            emoji.add(name)
        else:
            unknown.add(name)
    return sl, emoji, unknown


def fetch_shoelace(version: str) -> list[str]:
    with urllib.request.urlopen(SHOELACE_CDN.format(v=version), timeout=60) as r:
        data = json.load(r)
    icons = sorted({
        f["name"].split("/")[-1][:-4]
        for f in data.get("files", [])
        if f["name"].startswith("/cdn/assets/icons/") and f["name"].endswith(".svg")
    })
    if len(icons) < 1500:
        raise SystemExit(f"refusing to vendor a suspiciously small icon set ({len(icons)})")
    return icons


def fetch_gemoji() -> list[str]:
    req = urllib.request.Request(GEMOJI_API, headers={"User-Agent": "unionai-docs-icon-check"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    names = sorted(data)
    if len(names) < 1000:
        raise SystemExit(f"refusing to vendor a suspiciously small gemoji set ({len(names)})")
    return names


def update() -> int:
    version = pinned_version()
    if not version:
        print("check-icon-names: cannot find the Shoelace autoloader pin in baseof.html", file=sys.stderr)
        return 2
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sl = fetch_shoelace(version)
    (DATA_DIR / f"shoelace-{version}.txt").write_text("\n".join(sl) + "\n", encoding="utf-8")
    print(f"check-icon-names: vendored {len(sl)} Shoelace names for {version}")
    ge = fetch_gemoji()
    (DATA_DIR / "gemoji.txt").write_text("\n".join(ge) + "\n", encoding="utf-8")
    print(f"check-icon-names: vendored {len(ge)} gemoji aliases")
    return 0


def load(path: Path, what: str, remedy: str) -> set[str]:
    if not path.is_file():
        print(f"FATAL: no vendored {what} list at {path.name}. Refresh it:\n           {remedy}")
        raise SystemExit(1)
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


def main() -> int:
    if "--update" in sys.argv[1:]:
        return update()

    version = pinned_version()
    if not version:
        print("check-icon-names: cannot find the Shoelace autoloader pin in baseof.html", file=sys.stderr)
        return 2

    sl_codes, emoji_codes, unknown_codes = classify_shortcodes()
    if unknown_codes:
        print("FATAL: these shortcodes take an `icon=` parameter but neither render <sl-icon>")
        print("       nor Emojify it, so this check cannot tell which vocabulary they use:")
        for n in sorted(unknown_codes):
            print(f"         {n}.html")
        print("       Teach check_icon_names.py about them before merging.")
        return 1

    shoelace = load(DATA_DIR / f"shoelace-{version}.txt",
                    f"Shoelace {version}", "make update-icon-names")
    gemoji = load(DATA_DIR / "gemoji.txt", "gemoji", "make update-icon-names")

    others = set(ANY_SHOELACE.findall(BASEOF.read_text(encoding="utf-8"))) - {version}
    if others:
        print(f"WARNING: baseof.html pins more than one Shoelace version: {version} (autoloader) "
              f"plus {', '.join(sorted(others))}. Names checked against {version}.\n")

    # `{{< name ... icon="x" ... >}}`, including the `{{</* ... */>}}` display form —
    # a shortcode EXAMPLE with a dead name is how the corpus taught itself wrong ones.
    call = re.compile(r"\{\{[<%](?:/\*)?\s*([a-zA-Z0-9_-]+)\b([^}]*?)(?:\*/)?[>%]\}\}", re.S)
    attr = re.compile(r'icon="([^"]*)"')

    # Frontmatter `icon: name`, in the leading `---` block only. Same <sl-icon>,
    # same silent failure; the API generator emits one per generated page.
    fm_icon = re.compile(r'^icon:\s*"?([^"\n]*?)"?\s*$')

    roots = [Path(a) for a in sys.argv[1:] if not a.startswith("-")] or [Path("content")]
    bad: list[tuple[Path, int, str, str, str]] = []
    for root in roots:
        if not root.is_dir():
            print(f"check-icon-names: no such directory: {root}", file=sys.stderr)
            return 2
        for path in sorted(root.rglob("*.md")):
            lines = path.read_text(encoding="utf-8").split("\n")
            in_frontmatter = lines[:1] == ["---"]
            for lineno, line in enumerate(lines, 1):
                if in_frontmatter:
                    if lineno > 1 and line.rstrip() == "---":
                        in_frontmatter = False
                    else:
                        m = fm_icon.match(line)
                        if m and m.group(1) and m.group(1) not in shoelace:
                            bad.append((path, lineno, "frontmatter", m.group(1),
                                        f"Bootstrap Icons ({version})"))
                    continue
                for sc, params in call.findall(line):
                    m = attr.search(params)
                    if not m or not m.group(1):
                        continue          # absent or empty -> no icon emitted
                    name = m.group(1)
                    if sc in sl_codes and name not in shoelace:
                        bad.append((path, lineno, sc, name, f"Bootstrap Icons ({version})"))
                    elif sc in emoji_codes and name not in gemoji:
                        bad.append((path, lineno, sc, name, "gemoji alias"))

    if not bad:
        print(f"check-icon-names: OK (sl-icon: {', '.join(sorted(sl_codes))}; "
              f"gemoji: {', '.join(sorted(emoji_codes))}; plus frontmatter `icon:`)")
        return 0

    print("FATAL: these icon names do not exist in the set their shortcode resolves against.")
    print("       They fail silently — an empty slot, or a literal `:name:` — with no build error.")
    print("       Bootstrap Icons: https://icons.getbootstrap.com/   gemoji: https://api.github.com/emojis")
    print("")
    for path, lineno, sc, name, want in bad:
        where = f'icon: {name}' if sc == "frontmatter" else f'{{{{< {sc} … icon="{name}" >}}}}'
        print(f'  {path}:{lineno}: {where}  — not a {want}')
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
