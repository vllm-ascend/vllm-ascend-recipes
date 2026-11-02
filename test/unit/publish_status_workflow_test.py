from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-status.yml"


class PublishStatusWorkflowTests(unittest.TestCase):
    def test_pages_and_preview_refresh_audited_configuration_evidence_before_build(self) -> None:
        for name in ("deploy.yml", "preview-build.yml"):
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
                steps = workflow["jobs"]["build"]["steps"]
                refresh = next(index for index, step in enumerate(steps)
                               if "apply_verification_corrections.py" in step.get("run", ""))
                build = next(index for index, step in enumerate(steps) if step.get("name") == "Build")
                self.assertLess(refresh, build)
                command = steps[refresh]["run"]
                self.assertLess(command.index("seed_target_statuses.py"),
                                command.index("apply_verification_corrections.py"))
                self.assertLess(command.index("apply_verification_corrections.py"),
                                command.index("sync_single_node_target_status.py"))
                self.assertGreater(command.rindex("apply_verification_corrections.py"),
                                   command.index("sync_single_node_target_status.py"))

    def test_status_publication_deploys_latest_main_without_a_recursive_push(self) -> None:
        deployment = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8"))
        events = deployment.get("on", deployment.get(True))
        self.assertIn("workflow_run", events)
        self.assertIn("Publish Verification Status", events["workflow_run"]["workflows"])
        self.assertEqual(events["workflow_run"]["types"], ["completed"])
        build = deployment["jobs"]["build"]
        self.assertIn("github.event.workflow_run.conclusion == 'success'", build["if"])
        checkout = next(step for step in build["steps"] if step.get("name") == "Checkout")
        self.assertEqual(checkout["with"]["ref"], "main")

    def test_status_history_inputs_are_parsed_defensively(self) -> None:
        workflow = WORKFLOW.read_text()

        self.assertNotIn('--argjson last_pr', workflow)
        self.assertNotIn('--argjson last_nightly', workflow)
        self.assertNotIn('--argjson last_manual', workflow)
        self.assertNotIn('--argjson targets', workflow)
        self.assertIn('--arg last_pr', workflow)
        self.assertIn('--arg last_nightly', workflow)
        self.assertIn('--arg last_manual', workflow)
        self.assertIn('--arg targets', workflow)
        self.assertIn('($last_pr | fromjson? // null)', workflow)
        self.assertIn('($last_nightly | fromjson? // null)', workflow)
        self.assertIn('($last_manual | fromjson? // null)', workflow)
        self.assertIn('($targets | fromjson? // {})', workflow)


if __name__ == "__main__":
    unittest.main()
