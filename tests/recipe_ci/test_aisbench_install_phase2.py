from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/recipe_ci/install_aisbench.sh"
CONSTRAINTS = ROOT / "scripts/recipe_ci/aisbench-constraints.txt"


class AisbenchInstallerTests(unittest.TestCase):
    def test_installer_is_executable(self) -> None:
        self.assertTrue(os.access(INSTALLER, os.X_OK))

    def test_opencv_is_pinned_to_the_last_numpy_1_compatible_release(self) -> None:
        self.assertIn("--constraint", INSTALLER.read_text(encoding="utf-8"))
        self.assertEqual(
            CONSTRAINTS.read_text(encoding="utf-8").splitlines()[-1],
            "opencv-python-headless==4.11.0.86",
        )

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "benchmark"
        self.bin_dir = self.root / "bin"
        self.source.mkdir()
        self.bin_dir.mkdir()

        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(
            ["git", "-C", str(self.source), "config", "user.name", "Recipe CI"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.source),
                "config",
                "user.email",
                "recipe-ci@example.invalid",
            ],
            check=True,
        )
        (self.source / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(self.source), "commit", "-qm", "fixture"],
            check=True,
        )
        self.commit = subprocess.check_output(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(self.source), "remote", "add", "origin", "fixture://aisbench"],
            check=True,
        )

        command = self.bin_dir / "ais_bench"
        command.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        command.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "AIS_BENCH_ROOT": str(self.source),
                "AIS_BENCH_URL": "fixture://aisbench",
                "AIS_BENCH_EXPECTED_COMMIT": self.commit,
                "PATH": f"{self.bin_dir}:{environment['PATH']}",
            }
        )
        return environment

    def test_matching_install_is_reused(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER)],
            env=self.environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("already installed", result.stdout)
        self.assertIn(self.commit, result.stdout)

    def test_cached_source_is_reused_when_the_command_needs_installing(self) -> None:
        command = self.bin_dir / "ais_bench"
        command.unlink()
        fake_python = self.bin_dir / "python3"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "printf '#!/usr/bin/env bash\\nexit 0\\n' > \"$FAKE_AISBENCH_COMMAND\"\n"
            "chmod +x \"$FAKE_AISBENCH_COMMAND\"\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        environment = self.environment()
        environment["FAKE_AISBENCH_COMMAND"] = str(command)

        result = subprocess.run(
            ["bash", str(INSTALLER)],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Reusing cached AISBench source", result.stdout)
        self.assertTrue(command.exists())

    def test_clone_uses_http1_and_retries_before_publishing_the_cache(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("for attempt in 1 2 3", text)
        self.assertIn("git -c http.version=HTTP/1.1 clone", text)
        self.assertIn('clone_root="${AIS_BENCH_ROOT}.clone.$$"', text)
        self.assertIn('mv "$clone_root" "$AIS_BENCH_ROOT"', text)

    def test_wrong_commit_requires_force(self) -> None:
        environment = self.environment()
        environment["AIS_BENCH_EXPECTED_COMMIT"] = "0" * 40

        result = subprocess.run(
            ["bash", str(INSTALLER)],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("--force-reinstall", result.stdout)

    def test_tracked_source_changes_are_rejected(self) -> None:
        (self.source / "README.md").write_text("changed\n", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(INSTALLER)],
            env=self.environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("tracked files are modified", result.stdout)

    def test_force_reinstall_replaces_the_wrong_checkout(self) -> None:
        upstream = self.root / "upstream"
        subprocess.run(
            ["git", "clone", "-q", str(self.source), str(upstream)], check=True
        )
        subprocess.run(
            ["git", "-C", str(upstream), "tag", "fixture-tag"], check=True
        )
        target = self.root / "replacement"
        target.mkdir()
        (target / "wrong.txt").write_text("wrong\n", encoding="utf-8")
        fake_python = self.bin_dir / "python3"
        fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_python.chmod(0o755)
        environment = self.environment()
        environment.update(
            {
                "AIS_BENCH_ROOT": str(target),
                "AIS_BENCH_URL": str(upstream),
                "AIS_BENCH_TAG": "fixture-tag",
            }
        )

        result = subprocess.run(
            ["bash", str(INSTALLER), "--force-reinstall"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((target / "wrong.txt").exists())
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
            ).strip(),
            self.commit,
        )


if __name__ == "__main__":
    unittest.main()
