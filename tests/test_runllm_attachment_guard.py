#!/usr/bin/env python3
"""Tests for the RunLLM attachment preview guard."""

import json
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


GUARD_SCRIPT = Path(__file__).resolve().parent.parent / "static/js/runllm-attachment-guard.js"


class RunLLMAttachmentGuardTest(unittest.TestCase):
    def test_blocks_active_attachment_object_urls_only(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is required to execute the browser guard")

        node_script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");

            class TestBlob {
              constructor(parts, opts = {}) {
                this.type = opts.type || "";
              }
            }

            class TestFile extends TestBlob {
              constructor(parts, name, opts = {}) {
                super(parts, opts);
                this.name = name;
              }
            }

            const calls = [];
            global.window = global;
            global.Blob = TestBlob;
            global.URL = {
              createObjectURL: (object) => {
                calls.push({ type: object.type, name: object.name || "" });
                return `blob:${object.type}:${object.name || ""}`;
              }
            };

            vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));

            const results = {
              svgMime: URL.createObjectURL(
                new TestFile(["x"], "poc.svg", { type: "image/svg+xml" })
              ),
              htmlExtension: URL.createObjectURL(
                new TestFile(["x"], "poc.html", { type: "" })
              ),
              xmlMimeWithParameters: URL.createObjectURL(
                new TestFile(["x"], "poc.bin", { type: "text/xml; charset=utf-8" })
              ),
              png: URL.createObjectURL(
                new TestFile(["x"], "safe.png", { type: "image/png" })
              ),
              calls
            };

            console.log(JSON.stringify(results));
            """
        )

        completed = subprocess.run(
            ["node", "-e", node_script, str(GUARD_SCRIPT)],
            check=True,
            capture_output=True,
            text=True,
        )
        results = json.loads(completed.stdout)

        self.assertEqual(results["svgMime"], "blob:text/plain:")
        self.assertEqual(results["htmlExtension"], "blob:text/plain:")
        self.assertEqual(results["xmlMimeWithParameters"], "blob:text/plain:")
        self.assertEqual(results["png"], "blob:image/png:safe.png")

        self.assertEqual(results["calls"][0], {"type": "text/plain", "name": ""})
        self.assertEqual(results["calls"][1], {"type": "text/plain", "name": ""})
        self.assertEqual(results["calls"][2], {"type": "text/plain", "name": ""})
        self.assertEqual(results["calls"][3], {"type": "image/png", "name": "safe.png"})


if __name__ == "__main__":
    sys.exit(unittest.main())
