"""Audited corrections must not erase valid sibling or newer CI evidence."""

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class VerificationCorrectionsTests(unittest.TestCase):
    def test_repairs_only_the_audited_hardware_and_matching_history(self):
        script = ROOT / ".github/_scripts/apply_verification_corrections.py"
        self.assertTrue(script.is_file(), "audited false-positive runs need a reproducible correction")
        spec = importlib.util.spec_from_file_location("verification_corrections", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        selector = {"npu": "Ascend 950DT", "precision": "W8A8",
                    "deployment": "Single-Node", "case": "Serving"}
        a2 = {**selector, "npu": "Atlas 800I A2"}
        old = {"kind": "nightly", "workflow_run_id": 42, "status": "pass", "head_sha": "a" * 40}
        newer = {**old, "workflow_run_id": 43, "head_sha": "b" * 40}
        audit = [{"workflow_run_id": 42, "evidence_url": "https://example.invalid/runs/42/job/1",
                  "results": [{"recipe": "models/en/Test/model.yaml", "selector": selector,
                               "status": "fail", "reason": "Task returned HTTP 404"}]}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_dir = root / "status"
            status_dir.mkdir()
            params_dir = root / "runs" / old["head_sha"]
            params_dir.mkdir(parents=True)
            status_file = status_dir / "model.json"
            status_file.write_text(json.dumps({"model": "model", "last_nightly_run": old,
                "targets": {"a2": {"selector": a2, "last_nightly_run": old, "history": [old]},
                            "a5": {"selector": selector, "last_nightly_run": newer,
                                   "history": [newer, old]}}}), encoding="utf-8")
            params_file = params_dir / "model.params.json"
            snapshot = {"scenarios": [a2, selector], "hardware_runs": [
                {"hw_key": "atlas_800_a2", "status": "pass", "scenarios": [a2]},
                {"hw_key": "ascend_950dt", "status": "pass", "scenarios": [selector]}]}
            audit[0]["results"][0]["params_sha256"] = hashlib.sha256(json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            params_file.write_text(json.dumps(snapshot), encoding="utf-8")
            module.apply_corrections(status_dir, root / "runs", audit)
            result = json.loads(status_file.read_text(encoding="utf-8"))
            params = json.loads(params_file.read_text(encoding="utf-8"))
            self.assertEqual(result["last_nightly_run"]["status"], "fail")
            self.assertEqual(result["targets"]["a2"]["last_nightly_run"], old)
            self.assertEqual(result["targets"]["a5"]["last_nightly_run"], newer)
            self.assertEqual(result["targets"]["a5"]["history"][1]["status"], "fail")
            self.assertEqual(result["targets"]["a5"]["history"][1]["verification_evidence_url"], audit[0]["evidence_url"])
            self.assertEqual(params["hardware_runs"][0]["status"], "pass")
            self.assertEqual(params["hardware_runs"][1]["status"], "pass")
            self.assertEqual(params["scenarios"][1]["verification_status"], "fail")
            self.assertEqual(module.apply_corrections(status_dir, root / "runs", audit), [])

            # A later run may replace the archive at the same commit SHA.
            # Its exact snapshot must not be rewritten by the older audit.
            newer_snapshot = {**snapshot, "workflow_run_id": 43, "started_at": "later"}
            params_file.write_text(json.dumps(newer_snapshot), encoding="utf-8")
            module.apply_corrections(status_dir, root / "runs", audit)
            self.assertEqual(json.loads(params_file.read_text(encoding="utf-8")), newer_snapshot)

            # Legacy archives lack run IDs. Apply the exact audit again after
            # sync, so a newer PR snapshot cannot restore an older nightly pass.
            sync_spec = importlib.util.spec_from_file_location("correction_sync", ROOT /
                ".github/_scripts/sync_single_node_target_status.py")
            sync = importlib.util.module_from_spec(sync_spec)
            sync_spec.loader.exec_module(sync)
            pr = {**old, "kind": "pr", "workflow_run_id": 43}
            for modern in (False, True):
                later = {**snapshot, "recipe_path": "models/en/Test/model.yaml"}
                if modern:
                    later["workflow_run_id"] = 43
                params_file.write_text(json.dumps(later), encoding="utf-8")
                status_file.write_text(json.dumps({"model": "model", "last_nightly_run": old,
                    "last_pr_run": pr, "targets": {"a5": {"selector": selector,
                        "last_nightly_run": old, "last_pr_run": pr, "history": [old, pr]}}}), encoding="utf-8")
                targets = [{"id": "a5", "recipe": "models/en/Test/model.yaml", "selector": selector,
                            "mode": "single-node", "runner": "fixture"}]
                module.apply_corrections(status_dir, root / "runs", audit)
                sync.sync_target_status(status_dir, root / "runs", targets)
                module.apply_corrections(status_dir, root / "runs", audit)
                target = json.loads(status_file.read_text(encoding="utf-8"))["targets"]["a5"]
                self.assertEqual(target["last_nightly_run"]["status"], "fail")
                self.assertEqual(target["last_pr_run"]["status"], "pass")
                self.assertEqual(json.loads(params_file.read_text(encoding="utf-8")), later)

    def test_legacy_snapshot_preserves_unaffected_sibling_evidence(self):
        def load(name):
            spec = importlib.util.spec_from_file_location(name, ROOT / ".github/_scripts" / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        correction = load("apply_verification_corrections")
        sync = load("sync_single_node_target_status")
        selector = {"npu": "Ascend 950DT", "precision": "W4A4", "deployment": "Single-Node", "case": "Serving"}
        sibling = {**selector, "case": "Other serving"}
        recipe = "models/en/Test/model.yaml"
        run = {"kind": "nightly", "status": "pass", "workflow_run_id": 42, "head_sha": "a" * 40}
        snapshot = {"recipe_path": recipe, "scenarios": [selector, sibling]}
        audit = [{"workflow_run_id": 42, "evidence_url": "https://example.invalid/42", "results": [
            {"recipe": recipe, "selector": selector, "status": "fail", "reason": "no answer",
             "params_sha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True,
                 separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}]}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "status").mkdir()
            (root / "runs" / run["head_sha"]).mkdir(parents=True)
            status_file = root / "status/model.json"
            status_file.write_text(json.dumps({"model": "model", "last_nightly_run": run}), encoding="utf-8")
            (root / "runs" / run["head_sha"] / "model.params.json").write_text(json.dumps(snapshot), encoding="utf-8")
            correction.apply_corrections(root / "status", root / "runs", audit)
            targets = [{"id": name, "recipe": recipe, "selector": selected, "mode": "single-node", "runner": "fixture"}
                       for name, selected in (("audited", selector), ("sibling", sibling))]
            sync.sync_target_status(root / "status", root / "runs", targets)
            result = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(result["targets"]["audited"]["last_nightly_run"]["status"], "fail")
            self.assertEqual(result["targets"]["sibling"]["last_nightly_run"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
