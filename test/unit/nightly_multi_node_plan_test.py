"""Hosted multi-node selection must be validated before cluster allocation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLANNER = ROOT / ".github/_scripts/nightly_multi_node_plan.py"
ENTRY = ROOT / ".github/workflows/verify_multi_node.yaml"


class NightlyMultiNodePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipe = "models/en/Example/model.yaml"
        self.targets = [
            {"id": "alpha", "mode": "multi-node", "recipe": self.recipe,
             "test_id": "two-node", "runner": "unused", "selector": {}},
            {"id": "beta", "mode": "multi-node", "recipe": self.recipe,
             "test_id": "four-node", "runner": "unused", "selector": {}},
            {"id": "single", "mode": "single-node", "recipe": self.recipe,
             "runner": "unused", "selector": {}},
        ]
        self.write(".github/verification-targets.yaml", {"targets": self.targets})
        self.select([])
        self.write(self.recipe, {"scenarios": [
            {"test_id": "two-node", "deployment": "non-pd", "scripts": {}},
            {"test_id": "four-node", "deployment": "non-pd", "scripts": {}},
        ]})

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value), encoding="utf-8")

    def select(self, ids):
        self.write(".github/nightly-recipes.yaml", {
            "single_node": {"a2": [], "a3": [], "a5": []}, "multi_node": ids,
        })

    def run_plan(self, trigger="nightly", override=None):
        args = [sys.executable, str(PLANNER), "--root", str(self.root),
                "--trigger", trigger]
        if override is not None:
            args += ["--target-ids", override]
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")

    def plan(self, trigger="nightly", override=None):
        result = self.run_plan(trigger, override)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_rejected(self, diagnostic, trigger="nightly", override=None):
        result = self.run_plan(trigger, override)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "", "invalid selection must not emit a partial matrix")
        self.assertIn(diagnostic, result.stderr)

    def test_empty_daily_and_blank_manual_lists_allocate_no_jobs(self):
        for trigger, override in [("nightly", None), ("manual", None), ("manual", "  ")]:
            with self.subTest(trigger=trigger, override=override):
                self.assertEqual(self.plan(trigger, override), {"include": []})

    def test_daily_list_resolves_exact_registry_identities_in_order(self):
        self.select(["beta", "alpha"])
        self.assertEqual(self.plan(), {"include": [
            {"target_id": "beta", "recipe": self.recipe, "test_id": "four-node", "name": "beta"},
            {"target_id": "alpha", "recipe": self.recipe, "test_id": "two-node", "name": "alpha"},
        ]})
        self.assertEqual(self.plan("manual", ""), self.plan())

    def test_manual_override_replaces_daily_selection(self):
        self.select(["alpha"])
        self.assertEqual(self.plan("manual", " beta ")["include"], [
            {"target_id": "beta", "recipe": self.recipe, "test_id": "four-node", "name": "beta"},
        ])
        self.assertEqual([row["target_id"] for row in self.plan("manual", " beta, alpha ")["include"]],
                         ["beta", "alpha"])

    def test_nightly_cannot_override_and_pr_is_not_a_trigger(self):
        self.assert_rejected("manual", override="alpha")
        self.assert_rejected("invalid choice", trigger="pr")

    def test_unknown_duplicate_and_single_node_ids_fail_without_partial_output(self):
        for ids, message in [(["alpha", "missing"], "unknown target"),
                             (["alpha", "alpha"], "duplicate"),
                             (["single"], "multi-node")]:
            with self.subTest(ids=ids):
                self.select(ids)
                self.assert_rejected(message)
        self.assert_rejected("duplicate", "manual", "beta,beta")
        self.assert_rejected("target ID", "manual", "beta,,alpha")

    def test_ambiguous_registry_id_is_rejected(self):
        self.select(["alpha"])
        self.write(".github/verification-targets.yaml", {"targets": self.targets + [self.targets[0]]})
        self.assert_rejected("duplicate")

    def test_missing_or_nonexact_test_id_is_rejected(self):
        self.select(["alpha"])
        for test_id in [None, "", "two", "two-node "]:
            with self.subTest(test_id=test_id):
                self.targets[0]["test_id"] = test_id
                self.write(".github/verification-targets.yaml", {"targets": self.targets})
                self.assert_rejected("test_id")

    def test_explicit_multi_node_tests_are_matched_exactly(self):
        self.select(["alpha"])
        self.write(self.recipe, {"multi_node_tests": [{"test_id": "two-node"}]})
        self.assertEqual(self.plan()["include"][0]["test_id"], "two-node")
        self.write(self.recipe, {"multi_node_tests": [{"test_id": "other"}],
                                 "scenarios": [{"test_id": "two-node"}]})
        self.assert_rejected("test_id")

    def test_duplicate_recipe_test_id_is_rejected(self):
        self.select(["alpha"])
        self.write(self.recipe, {"scenarios": [{"test_id": "two-node"}, {"test_id": "two-node"}]})
        self.assert_rejected("test_id")

    def test_only_existing_english_recipe_paths_are_accepted(self):
        self.select(["alpha"])
        for path in ["models/zh/Example/model.yaml", "models/en/../../outside.yaml",
                     "models/en/Example/missing.yaml", "models/en/Example/model.txt",
                     str(self.root / self.recipe), "models/en/Example/../Example/model.yaml"]:
            with self.subTest(path=path):
                self.targets[0]["recipe"] = path
                self.write(".github/verification-targets.yaml", {"targets": self.targets})
                self.assert_rejected("recipe")

    def test_malformed_config_and_recipe_fail_closed(self):
        for selected in [None, "alpha", [1], [""]]:
            with self.subTest(selected=selected):
                self.select(selected)
                self.assert_rejected("multi_node")
        self.write(".github/nightly-recipes.yaml", {"a2": []})
        self.assert_rejected("multi_node")
        self.select(["alpha"])
        for value in [[], None, {"scenarios": "two-node"}, {"scenarios": ["two-node"]}]:
            with self.subTest(value=value):
                self.write(self.recipe, value)
                self.assert_rejected("recipe")

    def test_planning_never_runs_recipe_shell(self):
        self.select(["alpha"])
        marker = self.root / "recipe-was-executed"
        self.write(self.recipe, {"scenarios": [{"test_id": "two-node", "scripts": {
            "unsafe": {"language": "bash", "content": f"touch {marker.as_posix()}"},
        }}]})
        self.assertEqual(len(self.plan()["include"]), 1)
        self.assertFalse(marker.exists())

    def test_hosted_prepare_exports_matrix_count_and_selection_manifest(self):
        value = yaml.load(ENTRY.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = value["jobs"]["prepare"]["steps"]
        step = next(item for item in steps if item.get("id") == "plan")
        script_path = self.root / ".github/_scripts/nightly_multi_node_plan.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PLANNER, script_path)
        # Windows adaptation belongs to this local runner, never Linux production code.
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash"
        for trigger, override, selected in [("nightly", "", []),
                                            ("nightly", "", ["alpha"]),
                                            ("manual", "beta", ["beta"])]:
            with self.subTest(trigger=trigger, selected=selected):
                self.select([] if trigger == "manual" else selected)
                output = self.root / "github-output"
                output.write_text("", encoding="utf-8")
                env = {**os.environ, "GITHUB_OUTPUT": output.as_posix(),
                       "TRIGGER": trigger, "TARGET_IDS": override, "PYTHONUTF8": "1"}
                script = step["run"]
                if os.name == "nt":
                    python_dir = Path(sys.executable).parent.as_posix()
                    script = f'export PATH="{python_dir}:/usr/bin:/bin:$PATH"\n' + script
                result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", script],
                                        cwd=self.root, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertEqual(outputs["count"], str(len(selected)))
                self.assertEqual([row["target_id"] for row in json.loads(outputs["matrix"])["include"]], selected)
                self.assertEqual(json.loads((self.root / "selected-targets.json").read_text()),
                                 {"selected_targets": selected})


if __name__ == "__main__":
    unittest.main()
