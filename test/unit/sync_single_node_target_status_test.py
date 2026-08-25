from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
