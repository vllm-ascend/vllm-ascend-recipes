"""Exercise single-node startup deadlines and Linux process-group cleanup."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/recipe-server.sh"


@unittest.skipUnless(sys.platform == "linux", "requires Linux sessions and process groups")
class RecipeServerTests(unittest.TestCase):
    def shell(self, body, *, env=None, timeout=12):
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; ' + body, "test", str(HELPER)],
            cwd=ROOT, env={**os.environ, **(env or {})}, capture_output=True, text=True,
            timeout=timeout,
        )

    def test_default_and_custom_budgets_reach_the_server(self):
        for overrides, expected in [({}, "1800 2400"), ({
            "VLLM_ENGINE_READY_TIMEOUT_S": "1200", "RECIPE_SERVER_READY_TIMEOUT_S": "1500",
        }, "1200 1500")]:
            with self.subTest(overrides=overrides):
                result = self.shell(
                    'configure_recipe_server; bash -c \'echo "$VLLM_ENGINE_READY_TIMEOUT_S $RECIPE_SERVER_READY_TIMEOUT_S"\'',
                    env=overrides,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_invalid_or_conflicting_budgets_fail_before_launch(self):
        for inner, outer in [("0", "2400"), ("1800", "-1"), ("1800", "1800"),
                             ("1800", "600"), ("bad", "2400"), ("1800", "2.5")]:
            with self.subTest(inner=inner, outer=outer):
                result = self.shell("configure_recipe_server", env={
                    "VLLM_ENGINE_READY_TIMEOUT_S": inner, "RECIPE_SERVER_READY_TIMEOUT_S": outer,
                })
                self.assertNotEqual(result.returncode, 0)

    def test_delayed_readiness_uses_elapsed_seconds_and_custom_port(self):
        result = self.shell('''
configure_recipe_server
probe_file=$(mktemp)
trap 'stop_recipe_server; rm -f "$probe_file"' EXIT
start_recipe_server <(printf 'sleep 120\n')
sleep() { SECONDS=$((SECONDS + $1)); }
attempts=0
curl() { printf 'probe %s\n' "$*" > "$probe_file"; attempts=$((attempts + 1)); (( attempts >= 3 )); }
wait_recipe_server 8123
cat "$probe_file"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Server ready after 4s", result.stdout)
        self.assertIn("http://localhost:8123/v1/models", result.stdout)
        self.assertIn("--max-time", result.stdout)

    def test_monitoring_follows_seconds_including_probe_time(self):
        result = self.shell('''
configure_recipe_server
trap stop_recipe_server EXIT
start_recipe_server <(printf 'sleep 120\n')
sleep() { SECONDS=$((SECONDS + $1)); }
curl() { SECONDS=$((SECONDS + 1)); return 1; }
timeout() { shift 2; "$@"; }
npu-smi() { printf 'monitor-at:%s\n' "$SECONDS"; }
RECIPE_SERVER_READY_TIMEOUT_S=95
SECONDS=0
if wait_recipe_server 8123; then exit 91; fi
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("monitor-at:")],
                         ["monitor-at:30", "monitor-at:60", "monitor-at:90"])
        self.assertIn("within 95s", result.stdout + result.stderr)

    def test_hung_http_probe_is_bounded_by_the_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root / "server.sh"
            server.write_text("sleep 120\n")
            # A TCP listener accepts HTTP connections but never replies.
            listener = root / "listen.py"
            listener.write_text("import socket,time\ns=socket.socket()\ns.bind(('127.0.0.1',0))\n"
                                "s.listen()\nprint(s.getsockname()[1],flush=True)\n"
                                "c,a=s.accept()\ntime.sleep(120)\n")
            with subprocess.Popen([sys.executable, str(listener)], stdout=subprocess.PIPE, text=True) as process:
                port = process.stdout.readline().strip()
                try:
                    started = time.monotonic()
                    result = self.shell(f'configure_recipe_server; trap stop_recipe_server EXIT; '
                                        f'start_recipe_server "{server}"; wait_recipe_server {port}', env={
                        "VLLM_ENGINE_READY_TIMEOUT_S": "1", "RECIPE_SERVER_READY_TIMEOUT_S": "2",
                    })
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("within 2s", result.stdout + result.stderr)
                    self.assertLess(time.monotonic() - started, 8)
                finally:
                    process.kill()
                    process.wait()

    def test_server_exit_is_reported_without_waiting_for_the_deadline(self):
        result = self.shell('''
configure_recipe_server
trap stop_recipe_server EXIT
start_recipe_server <(printf 'exit 7\n')
curl() { return 1; }
wait_recipe_server 8123
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exit code 7", result.stdout + result.stderr)
        self.assertNotIn("within 2400s", result.stdout + result.stderr)

    def test_deadline_crossing_never_disables_the_monitor_timeout(self):
        result = self.shell('''
configure_recipe_server
SERVE_PID=$$
RECIPE_SERVER_READY_TIMEOUT_S=95
SECONDS=0
kill() { SECONDS=95; return 0; }
timeout() { echo MONITOR_SHOULD_NOT_RUN; }
if wait_recipe_server 8123; then exit 91; fi
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("MONITOR_SHOULD_NOT_RUN", result.stdout)
        self.assertIn("within 95s", result.stdout + result.stderr)

    def test_cleanup_does_not_wait_for_a_process_that_survives_sigkill(self):
        result = self.shell('''
SERVE_PID=424242
SERVER_OWNER_TOKEN=test
kill() { return 0; }
sleep() { :; }
timeout() { :; }
wait() { echo UNBOUNDED_WAIT; }
stop_recipe_server
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("UNBOUNDED_WAIT", result.stdout)

    def test_verifier_records_engine_exit_as_a_failed_scenario(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            weights = root / "cache/test/model"
            weights.mkdir(parents=True)
            (weights / "config.json").write_text("{}")
            for name, content in {
                "npu-smi": "exit 0",
                "curl": "exit 1",
                "vllm": 'if [[ "$1" == --version ]]; then echo fake; exit 0; fi; exit 7',
            }.items():
                executable = binary / name
                executable.write_text("#!/usr/bin/env bash\n" + content + "\n")
                executable.chmod(0o755)
            recipe = root / "fixture.yaml"
            recipe.write_text('''
meta:
  hardware: {atlas_800_a2: experimental}
model:
  model_id: test/model
scenarios:
  - npu: Atlas 800I A2
    precision: bf16
    deployment: Single-Node
    case: test
    steps:
      - content: |
          ```bash
          vllm serve your_model_path --port 8123
          ```
verification: |
  ```bash
  curl http://localhost:8123/v1/chat/completions
  ```
''')
            results = root / "results"
            result = subprocess.run(["bash", str(ROOT / "scripts/verify-recipe.sh"), str(recipe)],
                                    cwd=ROOT, env={**os.environ,
                "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                "RECIPE_CACHE_BASE": str(root / "cache"), "RECIPE_HW_KEY": "atlas_800_a2",
                "RECIPE_YAML_PATH": str(recipe),
                "RUN_PARAMS_DIR": str(results), "VLLM_ENGINE_READY_TIMEOUT_S": "1",
                "RECIPE_SERVER_READY_TIMEOUT_S": "5",
            }, capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue((results / "fixture.params.json").exists(), result.stdout + result.stderr)
            scenario = json.loads((results / "fixture.params.json").read_text())["scenarios"][0]
            self.assertEqual(scenario["verification_status"], "fail")
            self.assertIn("exit code 7", scenario["verification_reason"])

    def test_exit_and_term_cleanup_kill_descendants_without_harming_other_services(self):
        for mode in ["exit", "term", "int", "during-cleanup", "detached",
                     "group-cleanup", "launch-window", "leader-exit"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                script = root / "server.sh"
                child_file = root / "child.pid"
                group_file = root / "group.pid"
                cleanup_file = root / "cleanup.started"
                launch = "setsid sleep 120" if mode in ("detached", "leader-exit") else "sleep 120"
                finish = "exit 0" if mode == "leader-exit" else "wait"
                script.write_text(f'echo "$BASHPID" > "{group_file}"\ntrap "" TERM\n'
                                  f'{launch} &\necho $! > "{child_file}"\n{finish}\n')
                unrelated = subprocess.Popen(["sleep", "120"], start_new_session=True)
                supervisor = subprocess.Popen(["bash", "-c", f'''
set -euo pipefail
source "{HELPER}"
configure_recipe_server
trap stop_recipe_server EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
sleep() {{
  if [[ "$1" == 2 ]]; then echo started > "{cleanup_file}"; fi
  command sleep "$@"
}}
{'set -T; trap \'if [[ $BASH_COMMAND == SERVE_PID=* ]]; then trap - DEBUG; command sleep 0.2; kill -TERM "$$"; fi\' DEBUG' if mode == 'launch-window' else ''}
start_recipe_server "{script}"
echo "$SERVE_PID" > "{group_file}"
while [[ ! -f "{child_file}" ]]; do sleep 0.05; done
{'while true; do sleep 0.1; done' if mode in ('term', 'int') else 'exit 1'}
'''], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                group = child = None
                try:
                    deadline = time.monotonic() + 5
                    while not child_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(child_file.exists(), "test server did not start")
                    group = int(group_file.read_text())
                    child = int(child_file.read_text())
                    if mode in ("during-cleanup", "group-cleanup"):
                        while not cleanup_file.exists() and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertTrue(cleanup_file.exists(), "cleanup did not start")
                        if mode == "group-cleanup":
                            os.killpg(supervisor.pid, signal.SIGTERM)
                        else:
                            supervisor.terminate()
                    elif mode == "term":
                        supervisor.terminate()
                    elif mode == "int":
                        supervisor.send_signal(signal.SIGINT)
                    expected = 130 if mode == "int" else 143 if mode in (
                        "term", "during-cleanup", "group-cleanup", "launch-window") else 1
                    self.assertEqual(supervisor.wait(timeout=8), expected)
                    stat = Path(f"/proc/{child}/stat")
                    self.assertTrue(not stat.exists() or stat.read_text().split()[2] == "Z",
                                    "descendant survived cleanup")
                    self.assertIsNone(unrelated.poll(), "cleanup killed an unrelated process")
                finally:
                    if child:
                        try:
                            os.kill(child, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if group:
                        try:
                            os.killpg(group, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    supervisor.kill() if supervisor.poll() is None else None
                    supervisor.wait()
                    unrelated.kill()
                    unrelated.wait()


if __name__ == "__main__":
    unittest.main()
