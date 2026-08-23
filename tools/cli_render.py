#!/usr/bin/env python3
"""Render the CLI reference page from `flyte gen docs --type json`.

This is the half of the CLI doc generator that knows about Hugo, and it lives
here because that is knowledge about this site, not about the SDK. It used to
live in `flyte-sdk/src/flyte/cli/_gen.py`, where an SDK maintainer had no way to
verify a goldmark workaround or a shortcode name, and where a pure
docs-rendering bug needed an eng PR and an SDK release to reach readers
(DOC-1478 / DOC-1481).

The SDK now reports what the CLI *is*: commands, parameters, and the
distribution that provided each one. Who should see what is decided here, from
`cli-variants.toml`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import INFRA_ROOT  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

VARIANT_MAP_FILE = INFRA_ROOT / "cli-variants.toml"


def load_variant_map(path: Path) -> dict[str, frozenset[str]]:
    """Distribution -> the variants its commands are published to."""
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text()).get("distributions", {})
    return {
        dist: frozenset(v.split() if isinstance(v, str) else v)
        for dist, v in raw.items()
    }


def variants_for(distribution: str | None, variant_map, all_variants: frozenset[str]) -> frozenset[str]:
    """An unlisted distribution is published everywhere, so it needs no gate."""
    if distribution and distribution in variant_map:
        return variant_map[distribution]
    return all_variants


def format_help(text: str) -> str:
    """A command's help as Markdown.

    Indented blocks become fenced code blocks: indentation-based code does not
    survive the `{{< markdown >}}` shortcode's `RenderString`, and renders
    inconsistently when it does.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("    "):
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith("    ") or lines[i].strip() == ""):
                block.append(lines[i])
                i += 1
            while block and block[-1].strip() == "":
                block.pop()
            out.append("```bash")
            out.extend(textwrap.dedent("\n".join(block)).split("\n"))
            out.append("```")
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _escape_shortcodes(text: str) -> str:
    """Neutralise Hugo delimiters a CLI author wrote for their own reasons."""
    return text.replace("{{<", r"{{&lt;").replace("{{%", r"{{&percnt;")


def _declares_no_parameters(command: dict) -> bool:
    """True when the command declared nothing at all, so it gets no option table.

    `--help` is on every command, so an option table holding only that row says
    nothing. `declares_options` is reported by the generator rather than inferred
    from the presence of `--help`, which would assume it is the only thing click
    ever adds.
    """
    return not command["arguments"] and not command["declares_options"]


def render_command(command: dict, plugin_note: str | None) -> list[str]:
    """One command's section."""
    path = command["path"]
    parts = path.split(" ")
    out = [f"{'#' * (len(parts) + 1)} {path}"]

    if plugin_note:
        out.append("")
        out.append(f"> **Note:** This command is provided by the [`{plugin_note}`](#plugin-commands) plugin.")

    out.append("")
    usage = path
    if command["declares_options"]:
        usage += " [OPTIONS]"
    if command["is_group"]:
        usage += " COMMAND [ARGS]..."
    else:
        for arg in command["arguments"]:
            name = arg["name"].upper()
            usage += f" {name}" if arg["required"] else f" [{name}]"
    out.append(f"**`{usage}`**")

    if command["help"]:
        out.append("")
        out.append(format_help(command["help"]))

    if _declares_no_parameters(command):
        return out

    rows = []
    for opt in command["options"]:
        names = opt["opts"] + opt["secondary_opts"]
        rendered = " ".join(f"`{n}`" for n in names)
        default = f"`{opt['default']}`" if opt["default"] is not None else ""
        help_text = _escape_shortcodes(textwrap.dedent(opt["help"])) if opt["help"] else ""
        rows.append([rendered, f"`{opt['type']}`", default, help_text])

    if not rows:
        return out

    out.append("")
    out.append("| Option | Type | Default | Description |")
    out.append("|--------|------|---------|-------------|")
    for row in rows:
        out.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    return out


def build_index_table(groups, gated, is_verb_table: bool, include_gated: bool) -> list[str]:
    """A verb (Action/On) or noun (Object/Action) index table."""
    out = ["| Action | On |", "| ------ | -- |"] if is_verb_table else ["| Object | Action |", "| ------ | -- |"]

    for key, entries in groups.items():
        if is_verb_table:
            key_gated = gated.get(key, False)
            if key_gated and not include_gated:
                continue
            key_display = f"{key}⁺" if (key_gated and include_gated) else key

            filtered = [(n, g) for n, g in entries if include_gated or not g]
            if not filtered:
                # Listed with no subcommands to show: it takes none, or builds
                # them dynamically. Dropping it makes a documented command
                # unreachable from the index.
                out.append(f"| [`{key_display}`](#flyte-{key}) | - |")
            else:
                links = [
                    f"[`{n}⁺`](#flyte-{key}-{n})" if (g and include_gated) else f"[`{n}`](#flyte-{key}-{n})"
                    for n, g in filtered
                ]
                out.append(f"| `{key_display}` | {', '.join(links)}  |")
        else:
            filtered = [(v, g) for v, g in entries if include_gated or not g]
            if not filtered:
                continue
            links = [
                f"[`{v}⁺`](#flyte-{v}-{key})" if (g and include_gated) else f"[`{v}`](#flyte-{v}-{key})"
                for v, g in filtered
            ]
            out.append(f"| `{key}` | {', '.join(links)}  |")
    return out


def render(doc: dict, variant_map, plugin_variants: str | None, all_variants: frozenset[str]) -> str:
    """The whole page body."""
    commands = doc["commands"]

    # Which variants each command belongs to, and whether that is narrower than
    # the page as a whole -- the latter is what needs a gate around it.
    scope = {c["path"]: variants_for(c["distribution"], variant_map, all_variants) for c in commands}
    gated_paths = {p for p, v in scope.items() if v != all_variants}

    verb_groups: dict[str, list] = {}
    verb_gated: dict[str, bool] = {}
    noun_groups: dict[str, list] = {}
    for c in commands:
        parts = c["path"].split(" ")
        if len(parts) > 1:
            verb = parts[1]
            verb_gated.setdefault(verb, c["path"] in gated_paths)
            verb_groups.setdefault(verb, [])
            if len(parts) > 2:
                verb_groups[verb].append((parts[2], c["path"] in gated_paths))
        if len(parts) == 3:
            noun_groups.setdefault(parts[2], []).append((parts[1], c["path"] in gated_paths))

    has_gated = bool(gated_paths)
    wrap = bool(plugin_variants) and has_gated
    core_variants = " ".join(sorted(all_variants - set((plugin_variants or "").split())))

    out: list[str] = [""]

    def grid(noun_rows, verb_rows):
        return [
            "{{< grid >}}",
            "{{< markdown >}}",
            "\n".join(noun_rows),
            "{{< /markdown >}}",
            "{{< markdown >}}",
            "\n".join(verb_rows),
            "{{< /markdown >}}",
            "{{< /grid >}}",
        ]

    if wrap:
        out.append(f"{{{{< variant {core_variants} >}}}}")
        out += grid(
            build_index_table(noun_groups, verb_gated, False, include_gated=False),
            build_index_table(verb_groups, verb_gated, True, include_gated=False),
        )
        out.append("{{< /variant >}}")
        out.append(f"{{{{< variant {plugin_variants} >}}}}")
        out += grid(
            build_index_table(noun_groups, verb_gated, False, include_gated=True),
            build_index_table(verb_groups, verb_gated, True, include_gated=True),
        )
        out.append("{{< /variant >}}")
    else:
        out += grid(
            build_index_table(noun_groups, verb_gated, False, include_gated=True),
            build_index_table(verb_groups, verb_gated, True, include_gated=True),
        )

    if has_gated:
        dists = sorted({c["distribution"] for c in commands if c["path"] in gated_paths and c["distribution"]})
        notice = [
            "",
            "## Union-specific functionality {#plugin-commands}",
            "",
            "> [!NOTE]",
            f"> Commands marked with **⁺** are provided by the `{dists[0]}` plugin,",
            "> which adds Union-specific functionality to the Flyte CLI",
            "> (user management, RBAC, API keys).",
            f"> Install it with `pip install {dists[0]}`.",
            ">",
            "> See the [flyteplugins.union API reference](../union-plugin/_index)",
            "> for the programmatic interface.",
            "",
        ]
        if wrap:
            out.append("")
            out.append(f"{{{{< variant {plugin_variants} >}}}}")
            out.append("{{< markdown >}}")
            out.append("\n".join(notice))
            out.append("{{< /markdown >}}")
            out.append("{{< /variant >}}")
        else:
            out.append("\n".join(notice))

    out.append("")
    for c in commands:
        is_gated = c["path"] in gated_paths
        note = c["distribution"] if is_gated else None
        body = render_command(c, note)
        if wrap and is_gated:
            out.append("")
            out.append(f"{{{{< variant {plugin_variants} >}}}}")
            out.append("{{< markdown >}}")
            out.append("\n".join(body))
            out.append("{{< /markdown >}}")
            out.append("{{< /variant >}}")
        else:
            out.append("")
            out.append("\n".join(body))

    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin-variants", default=None, help="Variants that gated commands publish to.")
    parser.add_argument("--variants", default="flyte union", help="Every variant this page publishes to.")
    parser.add_argument("--variant-map", type=Path, default=VARIANT_MAP_FILE)
    args = parser.parse_args()

    doc = json.load(sys.stdin)
    sys.stdout.write(
        render(doc, load_variant_map(args.variant_map), args.plugin_variants, frozenset(args.variants.split()))
    )


if __name__ == "__main__":
    main()
