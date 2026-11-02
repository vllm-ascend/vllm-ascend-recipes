from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "_scripts" / "sync_single_node_target_status.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_single_node_target_status", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncSingleNodeTargetStatusTests(unittest.TestCase):
    def test_syncs_exact_passed_scenario_from_current_nightly_params(self) -> None:
        targets = [
            {
                "id": "qwen-a2-single-node",
                "recipe": "models/en/Qwen/Qwen3-30B-A3B.yaml",
                "mode": "single-node",
                "runner": "linux-aarch64-a2b4-8",
                "selector": {
                    "npu": "Atlas 800I A2",
                    "precision": "W8A8",
                    "deployment": "Single-Node",
                    "case": "Single Node Multi Card (A2)",
                },
            }
        ]
        run = {"kind": "nightly", "status": "pass", "head_sha": "a" * 40}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_dir = root / "status"
            run_dir = root / "runs" / run["head_sha"]
            status_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            (status_dir / "Qwen3-30B-A3B.json").write_text(
                json.dumps({"model": "Qwen3-30B-A3B", "last_pr_run": None, "last_nightly_run": run}),
                encoding="utf-8",
            )
            (run_dir / "Qwen3-30B-A3B.params.json").write_text(
                json.dumps(
                    {
                        "recipe_path": "models/en/Qwen/Qwen3-30B-A3B.yaml",
                        "scenarios": [targets[0]["selector"]],
                    }
                ),
                encoding="utf-8",
            )

            updated = load_module().sync_target_status(status_dir, root / "runs", targets)
            status = json.loads((status_dir / "Qwen3-30B-A3B.json").read_text(encoding="utf-8"))

        self.assertEqual(updated, ["Qwen3-30B-A3B"])
        self.assertEqual(
            status["targets"]["qwen-a2-single-node"]["last_nightly_run"], run
        )

    def test_syncs_recipe_failure_as_unknown_and_ignores_unknown_scenarios(self) -> None:
        target = {
            "id": "qwen-a2-single-node",
            "recipe": "models/en/Qwen/Qwen3-30B-A3B.yaml",
            "mode": "single-node",
            "runner": "linux-aarch64-a2b4-8",
            "selector": {
                "npu": "Atlas 800I A2",
                "precision": "W8A8",
                "deployment": "Single-Node",
                "case": "Single Node Multi Card (A2)",
            },
        }
        run = {"kind": "pr", "status": "fail", "head_sha": "b" * 40}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_dir = root / "status"
            run_dir = root / "runs" / run["head_sha"]
            status_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            (status_dir / "Qwen3-30B-A3B.json").write_text(
                json.dumps({"model": "Qwen3-30B-A3B", "last_pr_run": run, "last_nightly_run": None}),
                encoding="utf-8",
            )
            (run_dir / "Qwen3-30B-A3B.params.json").write_text(
                json.dumps(
                    {
                        "recipe_path": target["recipe"],
                        "scenarios": [target["selector"], {"npu": "Unknown"}],
                    }
                ),
                encoding="utf-8",
            )

            load_module().sync_target_status(status_dir, root / "runs", [target])
            status = json.loads((status_dir / "Qwen3-30B-A3B.json").read_text(encoding="utf-8"))

        self.assertEqual(
            status["targets"][target["id"]]["last_pr_run"],
            {**run, "status": "unknown"},
        )
        self.assertEqual(list(status["targets"]), [target["id"]])

    def test_does_not_backfill_skipped_or_scenarioless_runs(self) -> None:
        target = {
            "id": "qwen-a2-single-node",
            "recipe": "models/en/Qwen/Qwen3-30B-A3B.yaml",
            "mode": "single-node",
            "runner": "linux-aarch64-a2b4-8",
            "selector": {"npu": "Atlas 800I A2", "precision": "W8A8", "deployment": "Single-Node", "case": "Single Node Multi Card (A2)"},
        }
        run = {"kind": "nightly", "status": "skip", "head_sha": "c" * 40}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_dir = root / "status"
            run_dir = root / "runs" / run["head_sha"]
            status_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            (status_dir / "Qwen3-30B-A3B.json").write_text(
                json.dumps({"model": "Qwen3-30B-A3B", "last_pr_run": None, "last_nightly_run": run}),
                encoding="utf-8",
            )
            (run_dir / "Qwen3-30B-A3B.params.json").write_text(
                json.dumps({"recipe_path": target["recipe"], "scenarios": []}), encoding="utf-8"
            )

            updated = load_module().sync_target_status(status_dir, root / "runs", [target])
            status = json.loads((status_dir / "Qwen3-30B-A3B.json").read_text(encoding="utf-8"))

        self.assertEqual(updated, [])
        self.assertNotIn("targets", status)


class HardwareEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.status_dir = self.root / "status"
        self.status_dir.mkdir()
        self.status_file = self.status_dir / "Qwen.json"
        self.recipe = "models/en/Qwen/Qwen.yaml"
        self.targets = [
            {
                "id": "qwen-" + family,
                "recipe": self.recipe,
                "mode": "single-node",
                "runner": runner,
                "selector": {"npu": npu, "precision": "W8A8",
                             "deployment": "Single-Node", "case": "Serving"},
            }
            for family, npu, runner in (
                ("a2", "Atlas 800I A2", "linux-aarch64-a2b4-8"),
                ("a5", "Ascend 950DT", "linux-aarch64-a5-8"),
            )
        ]
        self.run = {"kind": "nightly", "status": "fail", "head_sha": "a" * 40,
                    "workflow_run_id": 42, "finished_at": "2026-10-20T02:00:00Z"}
        self.module = load_module()

    def hardware(self, index, status, scenarios=None):
        return {"hw_key": ("atlas_800_a2", "ascend_950dt")[index],
                "image": "example/image:v1", "status": status,
                "scenarios": [dict(self.targets[index]["selector"])] if scenarios is None else scenarios}

    def seed(self, run=None):
        run = run or {**self.run, "status": "pass", "workflow_run_id": 41,
                      "finished_at": "2026-10-19T02:00:00Z"}
        return {target["id"]: {"last_nightly_run": dict(run), "history": [dict(run)]}
                for target in self.targets}

    def sync(self, params, *, existing=None, run=None):
        run = run or self.run
        status = {"model": "Qwen", "last_" + run["kind"] + "_run": run,
                  "metadata": {"preserve": True}, **(existing or {})}
        self.status_file.write_text(json.dumps(status), encoding="utf-8")
        run_dir = self.root / "runs" / run["head_sha"]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "Qwen.params.json").write_text(
            json.dumps({"recipe_path": self.recipe, **params}), encoding="utf-8")
        updated = self.module.sync_target_status(self.status_dir, self.root / "runs", self.targets)
        result = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.assertEqual(result["metadata"], {"preserve": True})
        self.assertEqual(result["last_" + run["kind"] + "_run"], run)
        return updated, result

    def test_mixed_hardware_preserves_a2_pass_when_a5_fails(self):
        rows = [self.hardware(0, "pass"), self.hardware(1, "fail")]
        _, result = self.sync({"hardware_runs": rows,
                               "scenarios": [s for row in rows for s in row["scenarios"]]})
        self.assertEqual(result["targets"]["qwen-a2"]["last_nightly_run"]["status"], "pass")
        self.assertEqual(result["targets"]["qwen-a5"]["last_nightly_run"]["status"], "unknown")

    def test_skipped_aggregate_still_publishes_explicit_scenario_outcomes(self):
        scenarios = [{**target["selector"], "verification_status": outcome}
                     for target, outcome in zip(self.targets, ("pass", "skip"))]
        rows = [self.hardware(index, outcome, [scenarios[index]])
                for index, outcome in enumerate(("pass", "skip"))]
        _, result = self.sync({"hardware_runs": rows},
            existing={"targets": self.seed()}, run={**self.run, "status": "skip"})
        self.assertEqual(result["targets"]["qwen-a2"]["last_nightly_run"]["status"], "pass")
        self.assertEqual(result["targets"]["qwen-a5"]["last_nightly_run"]["status"], "skip")

    def test_scenario_evidence_does_not_inherit_another_hardwares_failure(self):
        scenario = {**self.targets[1]["selector"], "verification_status": "fail",
                    "verification_reason": "A5 startup failed",
                    "verification_evidence_url": "https://example.invalid/a5-job"}
        rows = [self.hardware(0, "pass"), self.hardware(1, "fail", [scenario])]
        run = {**self.run, "verification_note": "A5 startup failed",
               "verification_evidence_url": "https://example.invalid/a5-job"}
        _, result = self.sync({"hardware_runs": rows}, run=run)
        a2 = result["targets"]["qwen-a2"]["last_nightly_run"]
        a5 = result["targets"]["qwen-a5"]["last_nightly_run"]
        self.assertEqual(a2["status"], "pass")
        self.assertNotIn("verification_note", a2)
        self.assertNotIn("verification_evidence_url", a2)
        self.assertEqual(a5["verification_note"], scenario["verification_reason"])
        self.assertEqual(a5["verification_evidence_url"], scenario["verification_evidence_url"])

    def test_same_sha_snapshot_from_another_run_cannot_verify_this_run(self):
        seeded = self.seed()
        updated, result = self.sync({"workflow_run_id": 43,
            "hardware_runs": [self.hardware(0, "pass")]}, existing={"targets": seeded})
        self.assertEqual(updated, [])
        self.assertEqual(result["targets"], seeded)

    def test_missing_selected_hardware_clears_green_even_with_aggregate_unknown(self):
        for aggregate in ("fail", "unknown"):
            with self.subTest(aggregate=aggregate):
                rows = [self.hardware(0, "pass"), self.hardware(1, "unknown", [])]
                rows[1]["missing_reason"] = "selected hardware job did not provide results"
                updated, result = self.sync(
                    {"hardware_runs": rows, "scenarios": rows[0]["scenarios"]},
                    existing={"targets": self.seed()}, run={**self.run, "status": aggregate})
                self.assertEqual(updated, ["Qwen"])
                self.assertEqual(result["targets"]["qwen-a2"]["last_nightly_run"]["status"], "pass")
                self.assertEqual(result["targets"]["qwen-a5"]["last_nightly_run"]["status"], "unknown")

    def test_single_hardware_without_scenarios_clears_only_selected_targets(self):
        for state in ("pass", "fail", "unknown"):
            with self.subTest(state=state):
                seeded = self.seed()
                _, result = self.sync(
                    {"hw_key": "ascend_950dt", "scenarios": []},
                    existing={"targets": seeded}, run={**self.run, "status": state})
                self.assertEqual(result["targets"]["qwen-a5"]["last_nightly_run"]["status"], "unknown")
                self.assertEqual(result["targets"]["qwen-a2"], seeded["qwen-a2"])

    def test_explicit_scenario_status_overrides_hardware_and_aggregate(self):
        for aggregate in ("pass", "fail", "unknown"):
            for outcome in ("pass", "fail", "skip", "unknown"):
                with self.subTest(aggregate=aggregate, outcome=outcome):
                    scenario = {**self.targets[0]["selector"], "verification_status": outcome}
                    row = self.hardware(0, aggregate, [scenario])
                    _, result = self.sync({"hardware_runs": [row], "scenarios": [scenario]},
                                          run={**self.run, "status": aggregate})
                    actual = result.get("targets", {}).get("qwen-a2", {}).get("last_nightly_run", {})
                    self.assertEqual(actual.get("status"), outcome)

    def test_scenarios_on_same_failed_hardware_have_independent_results(self):
        other = copy.deepcopy(self.targets[0])
        other["id"] = "qwen-a2-other"
        other["selector"]["case"] = "Other serving"
        self.targets.append(other)
        scenarios = [
            {**self.targets[0]["selector"], "verification_status": "fail"},
            {**other["selector"], "verification_status": "pass"},
        ]
        _, result = self.sync({"hardware_runs": [self.hardware(0, "fail", scenarios)],
                               "scenarios": scenarios})
        self.assertEqual(result["targets"]["qwen-a2"]["last_nightly_run"]["status"], "fail")
        self.assertEqual(result["targets"]["qwen-a2-other"]["last_nightly_run"]["status"], "pass")

    def test_legacy_scenario_outcomes_and_explicit_status_without_hardware_runs(self):
        for aggregate, explicit, expected in (
            ("pass", None, "pass"), ("fail", None, "unknown"),
            ("unknown", None, "unknown"), ("pass", "fail", "fail"),
            ("unknown", "pass", "pass"),
        ):
            with self.subTest(aggregate=aggregate, explicit=explicit):
                scenario = dict(self.targets[0]["selector"])
                if explicit is not None:
                    scenario["verification_status"] = explicit
                _, result = self.sync({"scenarios": [scenario]},
                                      run={**self.run, "kind": "pr", "status": aggregate})
                actual = result.get("targets", {}).get("qwen-a2", {}).get("last_pr_run", {})
                self.assertEqual(actual.get("status"), expected)

    def test_disabled_unselected_and_unregistered_scenarios_are_untouched(self):
        self.targets[1]["enabled"] = False
        seeded = self.seed()
        unknown_scenario = {**self.targets[0]["selector"], "case": "Unregistered"}
        rows = [self.hardware(0, "pass", [unknown_scenario]), self.hardware(1, "pass")]
        updated, result = self.sync(
            {"hardware_runs": rows, "scenarios": [unknown_scenario, *rows[1]["scenarios"]]},
            existing={"targets": seeded})
        self.assertEqual(updated, [])
        self.assertEqual(result["targets"], seeded)

    def test_empty_disabled_hardware_does_not_clear_green(self):
        self.targets[1]["enabled"] = False
        seeded = self.seed()
        updated, result = self.sync(
            {"hardware_runs": [self.hardware(1, "unknown", [])], "scenarios": []},
            existing={"targets": seeded}, run={**self.run, "status": "unknown"})
        self.assertEqual(updated, [])
        self.assertEqual(result["targets"], seeded)

    def test_hardware_skip_and_unselected_hardware_keep_previous_evidence(self):
        seeded = self.seed()
        updated, result = self.sync(
            {"hardware_runs": [self.hardware(1, "skip")], "scenarios": []},
            existing={"targets": seeded})
        self.assertEqual(updated, [])
        self.assertEqual(result["targets"], seeded)

    def test_corrected_run_replaces_contradictory_history_regardless_of_timestamp(self):
        for timestamp in (self.run["finished_at"], "2026-10-20T01:00:00Z"):
            with self.subTest(timestamp=timestamp):
                old = {**self.run, "status": "pass", "finished_at": timestamp}
                _, result = self.sync(
                    {"scenarios": [self.targets[0]["selector"]]},
                    existing={"targets": self.seed(old)})
                target = result["targets"]["qwen-a2"]
                self.assertEqual(target["last_nightly_run"]["status"], "unknown")
                self.assertEqual(target["history"], [{**self.run, "status": "unknown"}])
                self.assertEqual(self.module.sync_target_status(
                    self.status_dir, self.root / "runs", self.targets), [])

    def test_replayed_older_run_corrects_history_without_overwriting_latest(self):
        newer = {**self.run, "status": "pass", "workflow_run_id": 43,
                 "finished_at": "2026-10-21T02:00:00Z"}
        seeded = self.seed(newer)
        seeded["qwen-a2"]["history"].append({**self.run, "status": "pass"})
        _, result = self.sync({"scenarios": [self.targets[0]["selector"]]},
                              existing={"targets": seeded})
        target = result["targets"]["qwen-a2"]
        self.assertEqual(target["last_nightly_run"], newer)
        self.assertEqual(target["history"], [newer, {**self.run, "status": "unknown"}])

    def test_replayed_older_run_without_timestamps_does_not_overwrite_latest(self):
        older = {key: value for key, value in self.run.items() if key != "finished_at"}
        newer = {**older, "status": "pass", "workflow_run_id": 43}
        _, result = self.sync({"scenarios": [self.targets[0]["selector"]]},
                              existing={"targets": self.seed(newer)}, run=older)
        self.assertEqual(result["targets"]["qwen-a2"]["last_nightly_run"], newer)

    def test_replayed_run_with_equal_timestamp_keeps_higher_workflow_id(self):
        newer = {**self.run, "status": "pass", "workflow_run_id": 43}
        _, result = self.sync({"scenarios": [self.targets[0]["selector"]]},
                              existing={"targets": self.seed(newer)})
        self.assertEqual(result["targets"]["qwen-a2"]["last_nightly_run"], newer)

    def test_history_correction_preserves_other_kind_and_legacy_runs(self):
        legacy = {key: value for key, value in self.run.items() if key != "workflow_run_id"}
        previous = {**legacy, "status": "pass", "finished_at": "2026-10-19T02:00:00Z"}
        pr = {**legacy, "kind": "pr", "status": "pass"}
        seeded = self.seed({**legacy, "status": "pass"})
        seeded["qwen-a2"]["last_pr_run"] = pr
        seeded["qwen-a2"]["history"].extend([previous, pr])
        _, result = self.sync({"scenarios": [self.targets[0]["selector"]]},
                              existing={"targets": seeded}, run=legacy)
        target = result["targets"]["qwen-a2"]
        self.assertEqual(target["last_pr_run"], pr)
        self.assertEqual(target["history"], [pr, {**legacy, "status": "unknown"}, previous])


if __name__ == "__main__":
    unittest.main()
