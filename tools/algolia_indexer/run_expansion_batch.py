#!/usr/bin/env python3
"""Run find_candidates over an expansions.json, compactly, for LLM judging."""
import json, sys, subprocess, pathlib
CONTENT = "/Users/ppiegaze/repos/unionai/unionai-docs/content"
exp = json.loads(pathlib.Path("expansions.json").read_text())
wanted = sys.argv[1:] or list(exp)
import find_candidates as fc
for q in wanted:
    terms = exp[q]
    pages = fc.scan(CONTENT, terms, primary=q)
    pages.sort(key=fc.rank_key)
    by = {}
    for p in pages:
        by.setdefault(p["group"], []).append(p)
    print(f"\n### {q!r}  [{', '.join(terms)}]")
    for g in ("guide", "reference", "tutorial", "other"):
        for p in by.get(g, [])[:2]:
            marks = "+".join(sorted(p["where"])) or "body"
            print(f"  {g:<9} {p['url'].replace(fc.URL_PREFIX,''):<52} "
                  f"{p['breadth']}/{len(terms)}t {p['total']:>3}h {marks}")
            print(f"            \"{p['title']}\"")
