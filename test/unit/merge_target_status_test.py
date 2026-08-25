from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "_scripts" / "merge_target_status.py"


def load_module():
    spec = importlib.util.spec_from_file_location("merge_target_status", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MergeTargetStatusTests(unittest.TestCase):
    def test_merging_one_target_preserves_other_targets_and_model_status(self) -> None:
        existing = {
            "model": "Qwen3-30B-A3B",
            "last_pr_run": {"status": "pass"},
            "last_nightly_run": {"status": "pass"},
            "targets": {
                "other": {"last_nightly_run": {"status": "pass"}},
                "qwen-a2": {"last_pr_run": {"status": "pass"}},
            },
        }
        target = {
            "id": "qwen-a2",
            "recipe": "models/en/Qwen/Qwen3-30B-A3B.yaml",
            "test_id": "qwen30b-dp-2n2c",
            "runner": "linux-aarch64-a2b4-1",
            "mode": "multi-node",
            "selector": {"npu": "Atlas 800I A2", "precision": "bf16", "deployment": "Multi-Node", "case": "2-node"},
        }
        run = {"status": "pass", "kind": "nightly"}

        merged = load_module().merge_target_status(existing, target, run, "nightly")

        self.assertEqual(merged["last_pr_run"], {"status": "pass"})
        self.assertEqual(merged["targets"]["other"], {"last_nightly_run": {"status": "pass"}})
        self.assertEqual(merged["targets"]["qwen-a2"]["test_id"], "qwen30b-dp-2n2c")
        self.assertEqual(merged["targets"]["qwen-a2"]["last_pr_run"], {"status": "pass"})
        self.assertEqual(merged["targets"]["qwen-a2"]["last_nightly_run"], run)


if __name__ == "__main__":
    unittest.main()
