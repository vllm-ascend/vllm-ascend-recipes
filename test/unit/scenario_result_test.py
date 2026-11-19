"""Scenario outcomes must describe executed checks, not merely planned commands."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ScenarioResultTests(unittest.TestCase):
    def record(self, params, index, status, reason=""):
        script = ROOT / ".github/_scripts/record_scenario_result.py"
        self.assertTrue(script.is_file(), "the verifier needs an explicit scenario result writer")
        spec = importlib.util.spec_from_file_location("record_scenario_result", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.params.json"
            path.write_text(json.dumps(params), encoding="utf-8")
            module.record_result(path, index, status, reason)
            return json.loads(path.read_text(encoding="utf-8"))

    def test_failed_check_cannot_mark_sibling_scenarios_failed(self):
        result = self.record({"scenarios": [{"index": 0}, {"index": 1}]}, 1, "fail", "HTTP check failed")
        self.assertNotIn("verification_status", result["scenarios"][0])
        self.assertEqual(result["scenarios"][1]["verification_status"], "fail")
        self.assertEqual(result["scenarios"][1]["verification_reason"], "HTTP check failed")

    def test_unknown_scenario_cannot_fabricate_execution_evidence(self):
        with self.assertRaises(ValueError):
            self.record({"scenarios": [{"index": 0}]}, 2, "pass")

    def test_invalid_outcome_is_rejected(self):
        with self.assertRaises(ValueError):
            self.record({"scenarios": [{"index": 0}]}, 0, "success")


if __name__ == "__main__":
    unittest.main()
