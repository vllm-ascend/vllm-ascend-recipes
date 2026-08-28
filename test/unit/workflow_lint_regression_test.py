from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "pr-recipe-verify.yml",
    ROOT / ".github" / "workflows" / "verify_multi_node.yaml",
)


class WorkflowLintRegressionTests(unittest.TestCase):
    def test_related_github_outputs_use_one_redirection_block(self) -> None:
        consecutive_redirects = re.compile(
            r'(?m)^(?:[ \t]*echo .*>> "\$GITHUB_OUTPUT"\n){2,}'
        )
        for workflow in WORKFLOWS:
            self.assertIsNone(
                consecutive_redirects.search(workflow.read_text()),
                f"{workflow} contains consecutive GITHUB_OUTPUT redirects",
            )


if __name__ == "__main__":
    unittest.main()