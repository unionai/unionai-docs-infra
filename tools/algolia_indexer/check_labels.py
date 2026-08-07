#!/usr/bin/env python3
"""Validate an edited label file before it is used as the eval's answer key.

A typo'd ideal URL scores as a permanent miss, so the eval would report a
ranking failure that is really a labelling failure. Cheap to catch, expensive
to debug later.

Usage:  check_labels.py queries.judged.json --content ../unionai-docs/content
"""
import argparse, json, sys
from pathlib import Path

GROUPS = {"guide", "reference", "tutorial", "other"}
P = "/docs/v2/union/"

ap = argparse.ArgumentParser()
ap.add_argument("labels")
ap.add_argument("--content", required=True)
a = ap.parse_args()

C = Path(a.content)
exists = lambda u: (C / u.replace(P, "").strip("/") / "_index.md").is_file() \
                or (C / (u.replace(P, "").strip("/") + ".md")).is_file()

data = json.loads(Path(a.labels).read_text())
errs, labelled, empty_groups, pending = [], 0, 0, []

for i, item in enumerate(data):
    q = item.get("q", f"<entry {i}>")
    ideal = item.get("ideal")
    if not isinstance(ideal, dict):
        errs.append(f"{q!r}: 'ideal' must be an object keyed by group"); continue
    if unknown := set(ideal) - GROUPS:
        errs.append(f"{q!r}: unknown group(s) {sorted(unknown)}")
    if missing := GROUPS - set(ideal):
        errs.append(f"{q!r}: missing group(s) {sorted(missing)} (use [] for 'nothing expected')")
    any_label = False
    for g, urls in ideal.items():
        if not isinstance(urls, list):
            errs.append(f"{q!r}/{g}: must be a list"); continue
        if not urls:
            empty_groups += 1
        for u in urls:
            any_label = True
            if not u.startswith(P):
                errs.append(f"{q!r}/{g}: {u!r} should start with {P}")
            elif not exists(u):
                errs.append(f"{q!r}/{g}: NO SUCH PAGE -> {u}")
    labelled += bool(any_label)
    if item.get("needs_human"):
        pending.append(q)

print(f"{len(data)} queries | {labelled} with at least one label | {empty_groups} empty groups")
if pending:
    print(f"{len(pending)} still marked needs_human: {', '.join(pending)}")
if errs:
    print(f"\n{len(errs)} PROBLEM(S):")
    for e in errs:
        print("  " + e)
    sys.exit(1)
print("\nOK — every labelled URL resolves to a real page.")
