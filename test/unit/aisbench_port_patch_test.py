"""Regression coverage for installed AISBench config discovery and port rewrite."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-recipe.sh"


class AISBenchPortPatchTests(unittest.TestCase):
    def test_config_discovery_uses_the_installed_python_package(self) -> None:
        # A Python patch-version update must not leave AISBench's path empty.
        script = VERIFY_SCRIPT.read_text(encoding="utf-8")
        assignment = re.search(r"^    AIS_CFG=(.*)$", script, re.MULTILINE)
        self.assertIsNotNone(assignment)
        with tempfile.TemporaryDirectory() as temp:
            site_packages = Path(temp) / "python3.12.16/site-packages"
            package = site_packages / "ais_bench/benchmark"
            package.mkdir(parents=True)
            (package.parent / "__init__.py").write_text("")
            (package / "__init__.py").write_text("")
            config = package / "configs/models/vllm_api/vllm_api_stream_chat.py"
            config.parent.mkdir(parents=True)
            config.write_text('models = [dict(path="", host_port=8080)]\n')
            value = assignment.group(1)
            if value.startswith("$("):
                # Execute the inline Python command without requiring a shell.
                command = shlex.split(value[2:-1])
                command[0] = sys.executable
                result = subprocess.run(
                    command,
                    env=dict(os.environ, PYTHONPATH=str(site_packages)),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                discovered = result.stdout.strip()
            else:
                discovered = shlex.split(value)[0]
            self.assertEqual(Path(discovered), config)

    def test_port_patch_replaces_any_default_port(self) -> None:
        script = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("host_port=8080", script)
        self.assertIn(
            'host_port[[:space:]]*=[[:space:]]*[0-9]+',
            script,
        )


if __name__ == "__main__":
    unittest.main()
