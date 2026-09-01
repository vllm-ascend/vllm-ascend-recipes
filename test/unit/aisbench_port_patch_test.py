"""Regression coverage for the AISBench server-port rewrite."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-recipe.sh"


class AISBenchPortPatchTests(unittest.TestCase):
    def test_port_patch_replaces_any_default_port(self) -> None:
        script = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("host_port=8080", script)
        self.assertIn(
            'host_port[[:space:]]*=[[:space:]]*[0-9]+',
            script,
        )


if __name__ == "__main__":
    unittest.main()
