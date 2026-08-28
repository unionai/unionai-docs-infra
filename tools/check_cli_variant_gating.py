#!/usr/bin/env python3
"""Fail when a plugin-provided CLI command is published to the wrong variants.

Why (DOC-1479): the generated CLI reference is one file serving two audiences.
Commands from `flyteplugins-union` must appear only inside `{{< variant union >}}`,
because they do not exist in an open-source install. The generator decides that
per command, and when it decides wrong the page documents a command the reader
cannot run.

That happened. `flyte fork` shipped ungated in unionai-docs#1483: the generator
classified a plugin-provided click.Group as core because the group dispatches to
subcommands and so has no callback (fixed in flyteorg/flyte-sdk#1463).

The reason this needs its own check, rather than a line in an existing one, is
that the defect ERASES ITS OWN EVIDENCE. One boolean in the generator drives
three outputs: the plus marker, the "provided by" note, and the variant gate.
A misclassification drops all three together, so the natural check --- "is every
marked command gated?" --- reads the broken page as clean. It would have passed
on #1483.

So this check never reads the generator's own markers. It asks the installed
distributions what they registered, via the `flyte.plugins.cli.commands` entry
points, and holds the rendered page to that. Entry points are packaging
metadata; they cannot inherit the generator's mistake.

Two things it deliberately gets right, both learned the hard way:

  * It covers SUBCOMMANDS. Only 4 of the 35 CLI entry points `flyteplugins-union`
    declares are top-level; the other 31 are dotted names like `get.api-key`
    that inject a subcommand into a CORE verb group. A top-level-only check
    would cover 11% of the surface and call it done.
  * It keys on the DISTRIBUTION, not on "is a plugin". `flyteplugins-hydra`
    ships inside flyte-sdk and is available to open-source users, so treating
    every plugin as Union-only is the same conflation pointing the other way ---
    it would hide an OSS command from the Flyte docs.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import INFRA_ROOT, get_repo_root  # noqa: E402

REPO_ROOT = get_repo_root()
VARIANT_MAP_FILE = INFRA_ROOT / "cli-variants.toml"

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

CLI_PLUGIN_ENTRY_POINT_GROUP = "flyte.plugins.cli.commands"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# Dumped inside the API venv, which is the only place the doc-generation
# plugins are installed.
PROBE = f"""
import json
from importlib.metadata import entry_points
out = []
for ep in entry_points(group={CLI_PLUGIN_ENTRY_POINT_GROUP!r}):
    dist = getattr(ep, "dist", None)
    out.append({{"name": ep.name, "dist": getattr(dist, "name", None)}})
print(json.dumps(out))
"""


def load_variant_map(path: Path) -> dict[str, frozenset[str]]:
    """Read the distribution -> variants policy from cli-variants.toml.

    This is deliberately NOT derived from the generator's own arguments. The
    generator decides gating from where a command's code was defined, and this
    check exists because that answer can be wrong; reading the policy back out of
    the same command would make the check agree with the generator by
    construction, which is how a note-based check passed on the #1483 leak.
    An independent statement of intent is the whole point of the file.
    """
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text()).get("distributions", {})
    out: dict[str, frozenset[str]] = {}
    for dist, variants in raw.items():
        if isinstance(variants, str):
            variants = variants.split()
        out[dist] = frozenset(variants)
    return out


def parse_all_variants(gen_command: str) -> frozenset[str]:
    """Recover the variant universe the page publishes to.

    Only the universe comes from the generator invocation; the per-distribution
    policy comes from cli-variants.toml (see load_variant_map).
    """
    argv = shlex.split(gen_command)
    i = 0
    while i < len(argv):
        arg = argv[i]
        value = None
        if "=" in arg and arg.startswith("--"):
            arg, _, value = arg.partition("=")
        elif i + 1 < len(argv):
            value = argv[i + 1]
        if arg == "--variants" and value:
            return frozenset(value.split())
        i += 1
    return frozenset({"flyte", "union"})


def expected_variants(
    dist: str | None,
    variant_map: dict[str, frozenset[str]],
    all_variants: frozenset[str],
) -> frozenset[str]:
    """Which variants a distribution's commands belong in.

    An unlisted distribution falls through to every variant, which means the
    check imposes no constraint on it. That is the deliberate default: a plugin
    nobody has classified is not assumed to be Union-only (that would hide an
    open-source plugin, the mirror image of the bug this guards). main() names
    the unlisted distributions so the gap is visible rather than silent.
    """
    if dist and dist in variant_map:
        return variant_map[dist]
    return all_variants


def heading_for(entry_point_name: str) -> str:
    """Map an entry-point name to the heading the generator emits for it.

    `fork` -> `flyte fork`; `get.api-key` -> `flyte get api-key`.
    """
    return "flyte " + entry_point_name.replace(".", " ", 1)


def gating_by_heading(markdown: str) -> dict[str, frozenset[str] | None]:
    """Map each command heading to the variants enclosing it, or None if ungated.

    Tracks shortcode nesting depth rather than pairing tags, so a command inside
    a nested block is still attributed to the variant that encloses it.
    """
    open_variant = re.compile(r"\{\{<\s*variant\s+([^>]+?)\s*>\}\}")
    close_variant = re.compile(r"\{\{<\s*/variant\s*>\}\}")
    heading = re.compile(r"^#{2,6}\s+(flyte\b.*?)\s*$")

    result: dict[str, frozenset[str] | None] = {}
    stack: list[frozenset[str]] = []

    for line in markdown.splitlines():
        stripped = line.strip()
        opened = open_variant.match(stripped)
        if opened:
            stack.append(frozenset(opened.group(1).split()))
            continue
        if close_variant.match(stripped):
            if stack:
                stack.pop()
            continue
        found = heading.match(line)
        if found:
            # Innermost enclosing variant wins; it is the narrower claim.
            result[found.group(1)] = stack[-1] if stack else None

    return result


def probe_entry_points(python: Path) -> list[dict[str, str | None]]:
    if not python.exists():
        sys.exit(
            f"ERROR: no API venv at {python}.\n"
            "       The CLI doc plugins are installed there, and without them this check\n"
            "       cannot see what a plugin registered. Create it with:\n"
            "         make -f unionai-docs-infra/Makefile.api.sdk setup-venv\n"
            "       (or pass --python to point at an environment that has them)."
        )
    proc = subprocess.run([str(python), "-c", PROBE], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"ERROR: could not read entry points from {python}:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def check_cli(
    cli: dict, python: Path, variant_map: dict[str, frozenset[str]], unlisted: set[str]
) -> list[str]:
    output_file = REPO_ROOT / cli["output_file"]
    if not output_file.exists():
        return [f"{cli['output_file']}: generated file not found"]

    all_variants = parse_all_variants(cli.get("gen_command", ""))
    gating = gating_by_heading(output_file.read_text())
    problems: list[str] = []

    for ep in probe_entry_points(python):
        name, dist = ep["name"], ep["dist"]
        if not dist or dist not in variant_map:
            # Unlisted: no policy, so no constraint. Recorded, not enforced.
            unlisted.add(dist or "(distribution unknown)")
            continue
        want = expected_variants(dist, variant_map, all_variants)

        head = heading_for(name)
        if head not in gating:
            # Not a leak: the generator can legitimately omit a command (e.g. a
            # dynamically-built group). Absent is not wrongly-published.
            continue

        # An ungated section renders for every reader, so no gate == all variants.
        got = gating[head] if gating[head] is not None else all_variants

        if not got <= want:
            leaked = " ".join(sorted(got - want))
            where = "EVERY variant" if gating[head] is None else f"variant(s) '{leaked}'"
            problems.append(
                f"{cli['output_file']}: '{head}' is published to {where}, "
                f"but {dist} ships only to {' '.join(sorted(want))}"
            )
        elif got < want:
            # The mirror image, and the reason the policy is stated here rather
            # than read back out of the generator: a command gated MORE narrowly
            # than its distribution ships is hidden from readers who can run it.
            missing = " ".join(sorted(want - got))
            problems.append(
                f"{cli['output_file']}: '{head}' is hidden from variant(s) '{missing}', "
                f"but {dist} ships to {' '.join(sorted(want))}"
            )

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--python",
        type=Path,
        default=VENV_PYTHON,
        help="Interpreter whose installed distributions define the expected surface.",
    )
    parser.add_argument(
        "--variant-map",
        type=Path,
        default=VARIANT_MAP_FILE,
        help="TOML file mapping a distribution to the variants its commands publish to.",
    )
    args = parser.parse_args()

    config = tomllib.loads((REPO_ROOT / "api-packages.toml").read_text())
    clis = [
        c
        for c in config.get("clis", [])
        if c.get("type") != "go" and "--plugin-variants" in c.get("gen_command", "")
    ]

    if not clis:
        print("check-cli-variant-gating: no variant-gated CLI docs configured; nothing to check")
        return

    variant_map = load_variant_map(args.variant_map)
    unlisted: set[str] = set()
    problems: list[str] = []
    for cli in clis:
        problems.extend(check_cli(cli, args.python, variant_map, unlisted))

    if unlisted:
        # Not a failure: an unlisted distribution is published to every variant by
        # policy. But "docs was never told about this plugin" and "this plugin is
        # genuinely for everyone" look identical from here, so say which it saw.
        print(
            "check-cli-variant-gating: not listed in "
            f"{args.variant_map.name}, so unconstrained: {', '.join(sorted(unlisted))}"
        )

    if problems:
        print("check-cli-variant-gating: plugin commands published to the wrong variants\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            f"\n{len(problems)} problem(s). A command from a Union-only distribution must sit inside\n"
            "a {{< variant union >}} block, or open-source readers are shown a command they cannot run.\n"
            "This usually means the generator misclassified it; see flyteorg/flyte-sdk#1463."
        )
        sys.exit(1)

    print("check-cli-variant-gating: all plugin commands correctly gated")


if __name__ == "__main__":
    main()
