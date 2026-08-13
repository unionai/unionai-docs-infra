#!/usr/bin/env python3
"""Vendor a patched @docsearch/js bundle.

WHY WE SELF-HOST A PATCHED COPY. DocSearch v5's Ask AI sends a request that
Algolia's own Agent Studio backend rejects:

    400 BadRequestError -- "This model does not support assistant message
    prefill. The conversation must end with a user message."

The user still gets a correct answer (the widget retries), but a red "Chat
error" banner sits above every reply. The cause is in the widget's own request
sanitizer, `sanitizeMessagesForRequest`:

    const parts = message.parts.filter((part) => !part.type.startsWith("data-"));

It strips `data-*` parts, but never drops a message left holding ZERO parts and
never drops a trailing assistant message. So an assistant message carrying only
`data-*` parts sanitizes down to an empty assistant message that still sits at
the end of the conversation -- exactly the shape the backend refuses.

It is not fixable from configuration:

  * `prepareSendMessagesRequest` -- the AI SDK hook that could rewrite the body --
    is constructed INSIDE the widget's `useAskAi`, not exposed as a prop.
  * It is not a model capability. Verified against claude-opus-5, claude-sonnet-5
    and claude-haiku-4-5: all three reject the same payload. Anthropic disallows
    assistant prefill when the request carries tools, and this agent always
    carries the search tool.
  * Disabling Agent Studio suggestions (in case `data-suggestions` was the part
    involved) does not help -- tested.

So we patch the shipped bundle and serve it ourselves.

WHAT THE PATCH DOES. It renames the sanitizer and wraps it, dropping any
trailing assistant message left with no parts. Deliberately narrow: it does not
drop trailing assistant messages that still carry content, because those are
legitimate in flows like regenerate.

HOW IT FINDS THE FUNCTION. The bundle ships minified, so the name is not stable
across releases (5.0.1 minifies it to `lA`). Anchoring on the name would break
silently on upgrade. Instead we anchor on the distinctive body -- the
`parts.filter(... startsWith('data-'))` expression -- walk back to the enclosing
`function`, and brace-match forward to its end. If the anchor is missing or
appears more than once, this script FAILS rather than emitting an unpatched
bundle: a silent no-op would restore the bug invisibly.

REMOVING THIS. When Algolia fixes the sanitizer upstream, delete this script,
delete assets/js/vendor/docsearch.js, and point search.html back at the CDN.
Check the release notes on upgrade -- `make update-docsearch` re-applies the
patch and will fail loudly if the upstream code has changed shape.

Usage:
    patch_docsearch.py --version 5.0.1 --out assets/js/vendor/docsearch.js
"""

import argparse
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.npmjs.org/@docsearch/js"
UA = {"User-Agent": "unionai-docs-infra/1.0"}

# The sanitizer's distinguishing expression. Backtick string literals because the
# bundle is minified with template literals.
ANCHOR = re.compile(r"\.parts\.filter\(\s*\w+\s*=>\s*!\s*\w+\.type\.startsWith\(`data-`\)\s*\)")


def fetch_umd(version):
    with urllib.request.urlopen(urllib.request.Request(f"{REGISTRY}/{version}", headers=UA)) as r:
        tarball = json.load(r)["dist"]["tarball"]
    with urllib.request.urlopen(urllib.request.Request(tarball, headers=UA)) as r:
        blob = r.read()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        member = tf.extractfile("package/dist/umd/index.js")
        return member.read().decode("utf-8")


def find_enclosing_function(src, at):
    """Return (name, start, end) of the `function NAME(...){...}` containing `at`."""
    start = src.rfind("function ", 0, at)
    if start < 0:
        raise SystemExit("ERROR: no enclosing `function` before the anchor.")
    m = re.match(r"function\s+(\w+)\s*\(", src[start:])
    if not m:
        raise SystemExit("ERROR: enclosing function is anonymous; cannot rename it safely.")
    name = m.group(1)
    brace = src.index("{", start + m.end() - 1)
    depth, i = 0, brace
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return name, start, i + 1
        i += 1
    raise SystemExit("ERROR: unbalanced braces while matching the sanitizer.")


def patch(src):
    hits = list(ANCHOR.finditer(src))
    if len(hits) != 1:
        raise SystemExit(
            f"ERROR: expected exactly 1 sanitizer anchor, found {len(hits)}.\n"
            "       The upstream bundle changed shape. Re-read\n"
            "       sanitizeMessagesForRequest in @docsearch/react and update ANCHOR --\n"
            "       do NOT ship an unpatched bundle."
        )
    name, start, end = find_enclosing_function(src, hits[0].start())
    original = src[start:end]
    inner = f"{name}__unpatched"
    wrapper = (
        original.replace(f"function {name}(", f"function {inner}(", 1)
        + f"function {name}(m){{"
        + f"var r={inner}(m);"
        # Drop trailing assistant messages left with no parts: the backend rejects a
        # conversation that does not end with a user message.
        + "while(r.length){var l=r[r.length-1];"
        + "if(l&&l.role===`assistant`&&(!l.parts||l.parts.length===0)){r=r.slice(0,-1);}else{break;}}"
        + "return r;}"
    )
    return src[:start] + wrapper + src[end:], name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="5.0.1")
    ap.add_argument("--out", default="assets/js/vendor/docsearch.js")
    args = ap.parse_args()

    src = fetch_umd(args.version)
    patched, name = patch(src)

    header = (
        f"/* @docsearch/js {args.version} -- VENDORED AND PATCHED. Do not edit by hand.\n"
        f" * Regenerate with: make update-docsearch\n"
        f" * Patch: wraps the request sanitizer (minified as `{name}` in this build) to drop\n"
        f" * trailing assistant messages left with no parts, which Agent Studio rejects with\n"
        f" * \"This model does not support assistant message prefill\".\n"
        f" * Rationale and removal conditions: tools/docsearch/patch_docsearch.py\n"
        f" */\n"
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + patched, encoding="utf-8")

    print(f"  upstream:  @docsearch/js@{args.version} ({len(src):,} bytes)")
    print(f"  sanitizer: {name}() -> wrapped as {name}__unpatched()")
    print(f"  wrote:     {out} ({len(header) + len(patched):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
