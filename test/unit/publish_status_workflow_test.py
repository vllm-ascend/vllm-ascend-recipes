from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-status.yml"


class PublishStatusWorkflowTests(unittest.TestCase):
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