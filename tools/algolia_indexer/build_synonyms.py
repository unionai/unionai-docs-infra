#!/usr/bin/env python3
"""Generate one-way Algolia synonyms from the v1->v2 migration tables.

Users keep typing Flyte 1 vocabulary -- `deck`, `pyflyte run`, `flytectl` --
against Flyte 2 docs that no longer contain those words, so the search returns
nothing even though the page they want exists under the new name. `deck` alone
is the 5th most-searched term on the site.

The rename map already exists and is maintained: the migration guide's
comparison tables. Generating synonyms from them keeps the alias list in sync
with the docs instead of drifting as a hand-kept list.

One-way is deliberate: searching the OLD term should surface the NEW page, but
searching the new term should not drag in v1 vocabulary.

Output is a DRAFT for review -- table cells hold prose and annotations as well
as identifiers, so every entry carries its source line for verification.

Usage:
    build_synonyms.py --migration ../unionai-docs/content/user-guide/migration/flyte-2 \
                      --out synonyms.draft.json
"""

import argparse
import json
import re
from pathlib import Path

OLD_HDR = re.compile(r"flyte\s*1|flytekit|^before$|^old$|v1", re.I)
NEW_HDR = re.compile(r"flyte\s*2|^after$|^new$|v2", re.I)
SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
SKIP_VALUES = {"n/a", "na", "-", "--", "both", "default", "", "none", "same"}


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _normalise(text):
    text = re.sub(r"\([^)]*\)", " ", text)        # drop (CLI), (constructor)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"=.*$", "", text)              # keyword=... -> keyword
    text = re.sub(r"[@]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;")

    if not text or text.lower() in SKIP_VALUES:
        return None
    if len(text.split()) > 3:                      # a sentence, not a term
        return None
    if len(text) < 2:
        return None
    return text


# Aliasing a common word is far worse than missing an alias: a wrong synonym on
# a high-traffic term corrupts a whole class of searches, while a missing one
# costs a single query. `workflow`, `task` and `map` are top-20 real queries --
# never let a table row hijack them.
STOPLIST = {
    "task", "tasks", "workflow", "workflows", "map", "env", "environment",
    "config", "run", "image", "images", "cache", "secret", "secrets", "app",
    "apps", "api", "deploy", "register", "packages", "commands", "copy",
    "builder", "requirements", "conditional", "dynamic", "trigger", "queue",
    "limits", "requests", "mem", "memory", "disk", "auth", "remote", "project",
    "domain", "org", "type", "types", "file", "files", "report", "reports",
    # These are real top-20 queries in the analytics; aliasing them to a
    # decorator (`timeout` -> `task`) would wreck a working search.
    "timeout", "timeouts", "docs", "retries", "retry", "cron", "dir", "cli flag",
    "connector", "trace", "traces", "spark", "ray", "gpu", "notification",
}


def cell_terms(cell):
    """Candidate terms from a cell: the plain-text label AND any backticked code.

    "Decks (`enable_deck=True`)" must yield `deck` -- the word a user types --
    not just `enable_deck`, which is the API parameter. Preferring backticks
    alone silently loses exactly the flagship rename this tool exists for.
    """
    terms = []
    plain = _normalise(re.sub(r"`[^`]+`", " ", cell))
    if plain:
        terms.append(plain)
    for tick in re.findall(r"`([^`]+)`", cell):
        t = _normalise(tick)
        if t:
            terms.append(t)
    return terms


def usable_input(term):
    """Reject inputs that would do more harm than good."""
    low = term.lower()
    if low in STOPLIST:
        return False, "common word"
    if len(low) < 4:
        return False, "too short"
    if re.fullmatch(r"-{1,2}\w", low):
        return False, "bare flag"
    return True, ""


def variants_of(term):
    """A term plus its plural / dotted-tail forms, so `deck` also covers `decks`."""
    out = {term, term.lower()}
    low = term.lower()
    if not low.endswith("s"):
        out.add(low + "s")
    else:
        out.add(low[:-1])
    if "." in low:                                 # flyte.report.Report -> Report
        out.add(low.rsplit(".", 1)[-1])
    return sorted({o for o in out if o})


def parse_tables(path):
    """Yield (old_cell, new_cell, lineno) from tables with old/new columns."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and SEP_RE.match(lines[i + 1]):
            header = cells(lines[i])
            old_i = next((n for n, h in enumerate(header) if OLD_HDR.search(h)), None)
            new_i = next((n for n, h in enumerate(header) if NEW_HDR.search(h)), None)
            i += 2
            if old_i is None or new_i is None or old_i == new_i:
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1
                continue
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                row = cells(lines[i])
                if max(old_i, new_i) < len(row):
                    yield row[old_i], row[new_i], i + 1
                i += 1
        else:
            i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--migration", required=True)
    ap.add_argument("--out", default="synonyms.draft.json")
    args = ap.parse_args()

    entries, seen, rejected = [], set(), []
    for path in sorted(Path(args.migration).glob("*.md")):
        for old_cell, new_cell, lineno in parse_tables(path):
            news = cell_terms(new_cell)
            if not news:
                continue
            targets = sorted({t for n in news for t in variants_of(n)})

            for old in cell_terms(old_cell):
                if old.lower() in (n.lower() for n in news):
                    continue
                ok, why = usable_input(old)
                if not ok:
                    rejected.append((old, why, f"{path.name}:{lineno}"))
                    continue
                key = old.lower()
                if key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "objectID": "v1v2-" + re.sub(r"[^a-z0-9]+", "-", key).strip("-"),
                    "type": "oneWaySynonym",
                    "input": key,
                    "synonyms": [t for t in targets if t.lower() != key],
                    "_also_matches": variants_of(old),
                    "_source": f"{path.name}:{lineno}",
                    "_old_cell": old_cell[:60],
                    "_new_cell": new_cell[:60],
                })

    entries = [e for e in entries if e["synonyms"]]
    Path(args.out).write_text(json.dumps(entries, indent=2), encoding="utf-8")

    print(f"{len(entries)} candidate synonyms -> {args.out}")
    print(f"{len(rejected)} inputs rejected as unsafe\n")
    for e in entries:
        print(f"  {e['input']:<26} -> {', '.join(e['synonyms'])[:44]:<46} [{e['_source']}]")
    if rejected:
        print("\nrejected (would have hijacked a common/ambiguous term):")
        for term, why, src in rejected[:14]:
            print(f"  {term:<26} {why:<14} [{src}]")
    print("\nREVIEW before pushing: table cells mix identifiers with prose.")


if __name__ == "__main__":
    main()
