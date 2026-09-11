#!/usr/bin/env python3
"""Guard what check_api_names.py treats as a real API name, and what it will
not report as a pass.

The rules under test come from real defects: `flyte.io.dataframe` in link text,
`flyte.File` for `flyte.io.File`, and three secret-manager pages that render
`import union` through a kit key. The SDK lookup is replaced by a stub, so these
tests never install anything.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_api_names import RESOLVER, Linkmap, run, spans  # noqa: E402

LINKMAP = {
    "packages": {"flyte": "/f/", "flyte.io": "/f/io/", "flyteplugins.polars": "/p/"},
    "identifiers": {"flyte.io.DataFrame": "/f/io/dataframe/",
                    "flyte.remote.Trigger": "/f/remote/trigger/",
                    "flyte.TaskEnvironment": "/f/taskenvironment/"},
    "methods": {"flyte.init": "/f/#init"},
}


def site(tmp_path, pages, linkmap=LINKMAP, exclude=None):
    content = tmp_path / "content"
    for rel, body in pages.items():
        p = content / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: T\n---\n" + body, encoding="utf-8")
    lm = tmp_path / "linkmap"
    lm.mkdir()
    (lm / "flytesdk-linkmap.json").write_text(json.dumps(linkmap))
    ex = None
    if exclude is not None:
        ex = tmp_path / ".api-names-exclude"
        ex.write_text(exclude)
    return content, lm, ex


def sdk(real=(), fails=False):
    def resolve(root, names):
        return None if fails else {n: n in real for n in names}
    return resolve


def check(tmp_path, capsys, pages, version="v2", resolver=None, exclude=None):
    content, lm, ex = site(tmp_path, pages, exclude=exclude)
    code = run(content, lm, ex, version, resolver or sdk(), {"flyte"})
    return code, capsys.readouterr().out


def test_linked_names_pass(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": (
        "Use `flyte.io.DataFrame`, `flyte.io.DataFrame.from_df`, `flyte.init()`,\n"
        "`@flyte.TaskEnvironment.task` and the `flyte.io` package.\n")},
        resolver=sdk(real={"flyte.io.DataFrame.from_df", "flyte.TaskEnvironment.task"}))
    assert code == 0, out
    assert "5 linked" in out


def test_a_name_that_does_not_exist_fails(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"g/dataframes.md": "Use `flyte.File`.\n"})
    assert code == 1
    assert "g/dataframes.md:4  flyte.File" in out


def test_link_text_is_checked(tmp_path, capsys):
    # The autolinker skips code already inside a link; the name must still be real.
    code, out = check(tmp_path, capsys, {"a.md": "[`flyte.io.dataframe`](../x/)\n"})
    assert code == 1 and "flyte.io.dataframe" in out


def test_code_blocks_front_matter_and_other_roots_are_ignored(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": (
        "```python\nx = `flyte.Nope`\n```\n~~~\n`flyte.Nope`\n~~~\n"
        "`env.task` `pd.DataFrame` `config.yaml`\n")})
    assert code == 0, out


def test_spans_skip_front_matter():
    text = "---\ntitle: `flyte.Nope`\n---\n`flyte.io`\n"
    assert list(spans(text)) == [(4, "flyte.io")]


def test_real_but_unlinked_is_a_note_not_a_failure(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": "`flyte.map.aio()`\n"},
                      resolver=sdk(real={"flyte.map.aio"}))
    assert code == 0, out
    assert "real but unlinked" in out and "flyte.map.aio" in out


def test_sdk_that_could_not_be_installed_is_not_a_pass(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": "`flyte.map.aio`\n"},
                      resolver=sdk(fails=True))
    assert code == 2
    assert "COULD NOT CHECK" in out


def test_plugin_names_outside_the_linkmap_are_reported_unchecked(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": "`flyteplugins.polars.Missing`\n"},
                      resolver=sdk(fails=True))
    assert code == 0, out
    assert "not checked" in out


def test_exclusions_match_page_and_name_and_stale_ones_are_reported(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"otel.md": "`flyte.run_name`\n"},
                      exclude="# OpenTelemetry keys\notel\\.md:flyte\\.run_name$\nnowhere:x\n")
    assert code == 0, out
    assert "1 excluded" in out
    assert "nowhere:x" in out


def test_kit_key_fails_on_v2_and_is_allowed_on_v1(tmp_path, capsys):
    page = {"secrets.md": "```python\nimport {{< key kit_import >}}\n```\n"}
    code, out = check(tmp_path, capsys, page, version="v2")
    assert code == 1 and "key kit_import" in out
    code, out = check(tmp_path / "v1", capsys, page, version="v1")
    assert code == 0, out


def test_flyte_1_names_are_reported_not_failed(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": (
        "`flytekit.map_task` becomes `flyte.io.DataFrame`. See union.ai: `union.ai`.\n")})
    assert code == 0, out
    assert "flytekit.map_task" in out and "union.ai" not in out.split("NOTE")[-1]


def test_release_notes_and_generated_reference_are_out_of_scope(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {
        "release-notes/_index.md": "`flyte.ai.agents.CodeModeAgent`\n",
        "api-reference/x.md": "`flyte.Nope`\n"})
    assert code == 0, out


def test_forced_links_need_a_target(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": (
        "`[[flyte.remote.Trigger|Trigger]]` `[[TaskEnvironment]]` `[[DataFrame.from_df]]`\n")})
    assert code == 0, out
    code, out = check(tmp_path / "b", capsys, {"a.md": "`[[NoSuchThing]]`\n"})
    assert code == 1 and "NoSuchThing" in out


def test_a_member_the_class_does_not_have_fails(tmp_path, capsys):
    # The autolinker links `Class.anything` once Class is documented; the member must be real.
    code, out = check(tmp_path, capsys, {"a.md": "`flyte.TaskEnvironment.nope`\n"})
    assert code == 1
    assert "member the class does not have" in out and "flyte.TaskEnvironment.nope" in out


def test_members_need_the_sdk_and_do_not_pass_without_it(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": "`flyte.TaskEnvironment.task`\n"},
                      resolver=sdk(fails=True))
    assert code == 2 and "COULD NOT CHECK" in out


def test_resolver_accepts_attributes_parameters_and_fields(tmp_path):
    # The real resolver script, run against a stand-in package rather than flyte.
    pkg = tmp_path / "fakesdk"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "import dataclasses\n"
        "class Timeout:\n"
        "    def __init__(self, max_runtime=None): pass\n"
        "    def run(self): pass\n"
        "@dataclasses.dataclass\n"
        "class Spec:\n"
        "    name: str\n"
        "def gpu(device, quantity=1): pass\n")
    names = ["fakesdk.Timeout.max_runtime", "fakesdk.Timeout.run", "fakesdk.Spec.name",
             "fakesdk.gpu.quantity", "fakesdk.gpu.count", "fakesdk.Timeout.nope",
             "fakesdk.Nope", "fakesdk.Timeout.run.x"]
    p = subprocess.run([sys.executable, "-c", RESOLVER], input=json.dumps(names),
                       capture_output=True, text=True, env={"PYTHONPATH": str(tmp_path)})
    result = json.loads(p.stdout.rsplit("@@RESULT@@", 1)[1])
    assert result == {
        "fakesdk.Timeout.max_runtime": True, "fakesdk.Timeout.run": True,
        "fakesdk.Spec.name": True, "fakesdk.gpu.quantity": True,
        "fakesdk.gpu.count": False, "fakesdk.Timeout.nope": False,
        "fakesdk.Nope": False, "fakesdk.Timeout.run.x": False}


def test_forced_link_in_a_table_cell(tmp_path, capsys):
    # Tables escape the pipe; the target is what comes before it.
    code, out = check(tmp_path, capsys, {"a.md": "| `[[flyte.remote.Trigger\\|Trigger]]` |\n"})
    assert code == 0, out


def test_opt_out_sigil_is_skipped(tmp_path, capsys):
    code, out = check(tmp_path, capsys, {"a.md": "`{{flyte.Nope}}`\n"})
    assert code == 0, out


def test_missing_linkmap_is_not_a_pass(tmp_path, capsys):
    content, lm, _ = site(tmp_path, {"a.md": "`flyte.io`\n"}, linkmap={})
    assert run(content, lm, None, "v2", sdk(), {"flyte"}) == 2


def test_linkmap_roots():
    assert Linkmap(packages=LINKMAP["packages"]).roots == {"flyte", "flyteplugins"}
