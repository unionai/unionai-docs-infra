#!/usr/bin/env python3
"""Diff two answer keys and explain WHY each label changed.

Re-judging produces a new key. Without a diff it silently replaces the
baseline, and the next eval's metric movement cannot be attributed: did search
get worse, or did the answer key change under it? That ambiguity defeats the
purpose of having a benchmark at all.

The classification that matters is content-driven vs judgment-driven:

  content   the page was renamed or deleted, so the label HAD to change. The
            benchmark tracked reality; nothing to review.
  judgment  the page still exists and we chose differently. This is a genuine
            baseline shift and must be reviewed before the new key is trusted.
  new       the page did not exist at the previous key's content commit.

Judgment changes are the ones that invalidate cross-run comparison. If there
are many, the two runs are not measuring the same thing and their scores
should not be compared -- the report says so rather than leaving it implied.

Usage:
    diff_labels.py old.json new.json --content ../unionai-docs/content
"""

import argparse
import json
import subprocess
from pathlib import Path

P = "/docs/v2/union/"
GROUPS = ("guide", "reference", "tutorial", "other")


def content_path(url, root):
    rel = url.replace(P, "").strip("/")
    for c in (root / rel / "_index.md", root / f"{rel}.md"):
        if c.is_file():
            return c
    return None


def added_since(root, base_sha):
    """Files added to content/ since base_sha, as a set of repo-relative paths."""
    if not base_sha:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--diff-filter=A", "--name-only",
             "--pretty=format:", f"{base_sha}..HEAD", "--", "."],
            capture_output=True, text=True, timeout=30)
        if out.returncode:
            return None
        return {line.strip() for line in out.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--content", required=True)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.content)
    old_doc = json.loads(Path(args.old).read_text())
    new_doc = json.loads(Path(args.new).read_text())

    def as_map(doc):
        items = doc.get("queries", doc) if isinstance(doc, dict) else doc
        return {i["q"]: i for i in items}

    base_sha = (old_doc.get("_meta", {}) or {}).get("content_sha") \
        if isinstance(old_doc, dict) else None
    new_files = added_since(root, base_sha)

    old, new = as_map(old_doc), as_map(new_doc)
    counts = {"content": 0, "judgment": 0, "new": 0, "invalid": 0}
    lines = []

    for q in sorted(set(old) | set(new)):
        if q not in old:
            lines.append(f"  + query added: {q!r}")
            continue
        if q not in new:
            lines.append(f"  - query removed: {q!r}")
            continue

        for g in GROUPS:
            was = set(old[q].get("ideal", {}).get(g, []))
            now = set(new[q].get("ideal", {}).get(g, []))
            for url in sorted(was - now):
                # still on disk => we simply chose differently
                if content_path(url, root):
                    counts["judgment"] += 1
                    lines.append(f"  {q!r}/{g}  JUDGMENT  dropped (page still exists): "
                                 f"{url.replace(P, '')}")
                else:
                    counts["content"] += 1
                    lines.append(f"  {q!r}/{g}  content   dropped (page gone): "
                                 f"{url.replace(P, '')}")
            for url in sorted(now - was):
                path = content_path(url, root)
                if path is None:
                    # An added label pointing at nothing is a broken key, not a
                    # judgment call. check_labels.py fails on it too, but the
                    # differ must not quietly file it as a baseline shift.
                    counts["invalid"] += 1
                    lines.append(f"  {q!r}/{g}  INVALID   added but NO SUCH PAGE: "
                                 f"{url.replace(P, '')}")
                    continue
                rel = str(path.relative_to(root))
                is_new = new_files is not None and any(f.endswith(rel) for f in new_files)
                kind = "new" if is_new else "judgment"
                counts[kind] += 1
                label = "new page" if is_new else "JUDGMENT  added (page pre-existed)"
                lines.append(f"  {q!r}/{g}  {label}: {url.replace(P, '')}")

    total = sum(counts.values())
    print(f"{len(old)} -> {len(new)} queries | {total} label change(s)")
    print(f"  content-driven : {counts['content']}  (page renamed or deleted)")
    print(f"  new pages      : {counts['new']}")
    print(f"  JUDGMENT-driven: {counts['judgment']}  <- review these")
    if counts["invalid"]:
        print(f"  INVALID        : {counts['invalid']}  <- broken key, fix before use")
    if base_sha is None:
        print("\n  note: old key has no _meta.content_sha, so 'new page' could not be"
              "\n        distinguished from a judgment change; both show as judgment.")

    if lines and not args.quiet:
        print()
        for line in lines:
            print(line)

    if counts["judgment"]:
        print(f"\n{counts['judgment']} judgment change(s): the two keys do NOT measure the"
              "\nsame thing. Scores from runs using different keys are not comparable"
              "\nuntil these are reviewed.")
    else:
        print("\nNo judgment changes -- cross-run scores remain comparable.")


if __name__ == "__main__":
    main()
