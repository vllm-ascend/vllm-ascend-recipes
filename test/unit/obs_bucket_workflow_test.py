"""Regression coverage for the nightly OBS artifact destination."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "_recipe_verify.yml"


class ObsBucketWorkflowTests(unittest.TestCase):
    def test_nightly_obs_upload_uses_mindx_package_bucket(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        upload_step = workflow.split("- name: Upload verification results to OBS (Nightly only)", 1)[1]

        self.assertIn("bucket: mindx-package", upload_step)


if __name__ == "__main__":
    unittest.main()
