from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "_scripts" / "verification_targets.py"
FILL_SCRIPT = ROOT / ".github" / "_scripts" / "fill_missing_target_results.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verification_targets", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_fill_module():
    spec = importlib.util.spec_from_file_location("fill_missing_target_results", FILL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {FILL_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VerificationTargetTests(unittest.TestCase):
    def test_pr34_recipe_test_ids_have_unique_targets(self) -> None:
        targets = load_module().load_targets(ROOT / ".github" / "verification-targets.yaml")

        self.assertEqual(len({target["id"] for target in targets}), len(targets))
        self.assertEqual(
            load_module().find_target(
                targets,
                "models/en/DeepSeek/DeepSeek-V2-Lite-W8A8.yaml",
                "dsv2lite-pd-2n2c",
            )["id"],
            "deepseek-v2-lite-a2-w8a8-1p1d",
        )
        self.assertEqual(
            load_module().find_target(
                targets,
                "models/en/Qwen/Qwen3-30B-A3B.yaml",
                "qwen30b-dp-2n2c",
            )["id"],
            "qwen3-30b-a3b-a2-bf16-2node",
        )

    def test_pr34_jobs_and_status_publisher_use_target_ids(self) -> None:
        targets = load_module().load_targets(ROOT / ".github" / "verification-targets.yaml")
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "verify_multi_node.yaml").read_text())
        reusable = yaml.safe_load((ROOT / ".github" / "workflows" / "_verify_multi_node.yaml").read_text())
        publisher = (ROOT / ".github" / "workflows" / "publish-status.yml").read_text()

        for job in workflow["jobs"].values():
            if not isinstance(job, dict) or "uses" not in job:
                continue
            inputs = job["with"]
            target = load_module().find_target(targets, inputs["recipe"], inputs["test_id"])
            self.assertIsNotNone(target)
            self.assertEqual(inputs["target_id"], target["id"])

        self.assertIn("target_id", reusable[True]["workflow_call"]["inputs"])
        self.assertIn("Multi-node framework", publisher)
        self.assertIn("verification-target-results-", publisher)

    def test_manual_multi_node_run_is_not_classified_as_pr_evidence(self) -> None:
        publisher = (ROOT / ".github" / "workflows" / "publish-status.yml").read_text()

        self.assertRegex(
            publisher,
            r'elif \[ "\$RW_EVENT" = "workflow_dispatch" \]; then\s+TRIGGER="manual"',
        )

    def test_missing_nightly_target_result_is_materialized_as_failure(self) -> None:
        targets = load_module().load_targets(ROOT / ".github" / "verification-targets.yaml")
        known = targets[0]
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)
            existing = results / f'{known["id"]}.json'
            existing.write_text(json.dumps({"target_id": known["id"], "status": "pass"}), encoding="utf-8")

            load_fill_module().fill_missing_results(targets, results)

            self.assertEqual(json.loads(existing.read_text(encoding="utf-8"))["status"], "pass")
            missing = json.loads((results / f'{targets[1]["id"]}.json').read_text(encoding="utf-8"))
            self.assertEqual(missing["status"], "fail")
            self.assertEqual(missing["recipe"], targets[1]["recipe"])
            self.assertEqual(missing["test_id"], targets[1]["test_id"])

    def test_selected_missing_target_result_does_not_mark_skipped_targets_failed(self) -> None:
        targets = load_module().load_targets(ROOT / ".github" / "verification-targets.yaml")
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)

            load_fill_module().fill_missing_results(targets, results, {targets[1]["id"]})

            self.assertTrue((results / f'{targets[1]["id"]}.json').exists())
            self.assertFalse((results / f'{targets[0]["id"]}.json').exists())

    def test_status_publisher_backfills_missing_nightly_targets(self) -> None:
        publisher = (ROOT / ".github" / "workflows" / "publish-status.yml").read_text()

        self.assertIn("cancel-in-progress: false", publisher)
        self.assertIn("github.event.workflow_run.name == 'Multi-node framework' ||", publisher)
        self.assertIn("fill_missing_target_results.py", publisher)
        self.assertIn('echo "workflow_name=$RW_NAME"', publisher)
        self.assertIn(
            'if [ "$TRIGGER" = "nightly" ] && [ "$WORKFLOW_NAME" = "Multi-node framework" ]; then',
            publisher,
        )
        self.assertIn('actions/runs/${RUN_ID}/jobs?per_page=100', publisher)
        self.assertIn('--target-id "$target_id"', publisher)


if __name__ == "__main__":
    unittest.main()
