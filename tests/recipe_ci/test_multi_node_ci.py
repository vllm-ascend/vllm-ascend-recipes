from __future__ import annotations

import os
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.recipe_ci.plan import PlanError, load_hosts, load_plan  # noqa: E402


EXAMPLE = ROOT / "configs/recipe_ci/plans/deepseek-v2-lite-pd-2n2c"
GENERIC_DP_EXAMPLE = ROOT / "configs/recipe_ci/plans/qwen3-30b-a3b-dp-2n2c"
DEEPSEEK_V4_EXAMPLE = (
    ROOT / "configs/recipe_ci/plans/deepseek-v4-flash-a2-pd-reduced"
)
QWEN35_EXAMPLE = ROOT / "configs/recipe_ci/plans/qwen3.5-27b-a2-pd-reduced"


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


class PlanTests(unittest.TestCase):
    def test_example_has_two_independent_two_instance_nodes(self) -> None:
        plan = load_plan(EXAMPLE / "plan.yaml")

        self.assertEqual(plan.name, "deepseek-v2-lite-pd-2n2c")
        self.assertEqual(plan.leader.id, "node0")
        self.assertEqual([node.role for node in plan.nodes], ["prefill", "decode"])
        self.assertEqual([node.index for node in plan.nodes], [0, 1])
        self.assertEqual([node.readiness.count for node in plan.nodes], [2, 2])
        self.assertEqual(
            [node.launch for node in plan.nodes],
            ["nodes/node0/run.sh", "nodes/node1/run.sh"],
        )
        self.assertEqual(plan.gateway.port, 38085)
        self.assertEqual(len(plan.evaluations.accuracy), 1)
        self.assertEqual(len(plan.evaluations.performance), 1)

    def test_each_node_requires_its_own_launch_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copied)
            raw = yaml.safe_load((copied / "plan.yaml").read_text(encoding="utf-8"))
            raw["nodes"][1]["launch"] = "nodes/node0/run.sh"
            (copied / "plan.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

            with self.assertRaisesRegex(PlanError, "each node must have its own"):
                load_plan(copied / "plan.yaml")

    def test_generic_dp_example_has_one_four_rank_group(self) -> None:
        plan = load_plan(GENERIC_DP_EXAMPLE / "plan.yaml")
        api_run = (GENERIC_DP_EXAMPLE / plan.nodes[0].launch).read_text(
            encoding="utf-8"
        )
        headless_run = (GENERIC_DP_EXAMPLE / plan.nodes[1].launch).read_text(
            encoding="utf-8"
        )

        self.assertEqual(plan.name, "qwen3-30b-a3b-dp-2n2c")
        self.assertEqual([node.id for node in plan.nodes], ["node0", "node1"])
        self.assertEqual([node.role for node in plan.nodes], ["api", "headless"])
        self.assertEqual(plan.nodes[0].readiness.count, 1)
        self.assertIsNone(plan.nodes[1].readiness)
        self.assertIsNone(plan.gateway)
        self.assertIn("--data-parallel-size 4", api_run)
        self.assertIn("--data-parallel-size-local 2", api_run)
        self.assertIn("--headless", headless_run)
        self.assertIn("--data-parallel-start-rank 2", headless_run)
        self.assertIn(
            '--data-parallel-address "$RECIPE_NODE_0_IP"', headless_run
        )

    def test_examples_keep_aisbench_model_config_in_the_plan(self) -> None:
        for example in (
            EXAMPLE,
            GENERIC_DP_EXAMPLE,
            DEEPSEEK_V4_EXAMPLE,
            QWEN35_EXAMPLE,
        ):
            accuracy_config = example / "aisbench/models/vllm_api_general_chat.py"
            performance_config = example / "aisbench/models/vllm_api_stream_chat.py"
            accuracy_script = (example / "evaluations/accuracy.sh").read_text(
                encoding="utf-8"
            )
            performance_script = (example / "evaluations/performance.sh").read_text(
                encoding="utf-8"
            )

            self.assertTrue(accuracy_config.is_file())
            self.assertTrue(performance_config.is_file())
            self.assertIn("$RECIPE_PLAN_DIR/aisbench", accuracy_script)
            self.assertIn("vllm_api_general_chat", accuracy_script)
            self.assertIn("$RECIPE_PLAN_DIR/aisbench", performance_script)
            self.assertIn("vllm_api_stream_chat", performance_script)
            for config in (accuracy_config, performance_config):
                text = config.read_text(encoding="utf-8")
                self.assertNotIn("import os", text)
                self.assertIn("path=__RECIPE_MODEL_PATH__", text)
                self.assertIn("host_port=__RECIPE_ENDPOINT_PORT__", text)
            self.assertIn("render-model-config", accuracy_script)
            self.assertIn("render-model-config", performance_script)

    def test_lightweight_plan_carries_an_offline_gsm8k_fixture(self) -> None:
        dataset = EXAMPLE / "aisbench/datasets/gsm8k"
        train_rows = [
            json.loads(line)
            for line in (dataset / "train.jsonl").read_text().splitlines()
        ]
        test_rows = [
            json.loads(line)
            for line in (dataset / "test.jsonl").read_text().splitlines()
        ]

        self.assertGreaterEqual(len(train_rows), 1)
        self.assertEqual(len(test_rows), 8)
        for row in [*train_rows, *test_rows]:
            self.assertEqual(set(row), {"question", "answer"})
            self.assertIn("#### ", row["answer"])

        for script_name in ("accuracy.sh", "performance.sh"):
            script = (EXAMPLE / "evaluations" / script_name).read_text()
            self.assertIn("prepare_gsm8k.sh", script)

    def test_hosts_must_match_plan_nodes(self) -> None:
        plan = load_plan(EXAMPLE / "plan.yaml")
        with tempfile.TemporaryDirectory() as directory:
            hosts_path = Path(directory) / "hosts.yaml"
            hosts_path.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "hosts": {
                            "node0": {"address": "127.0.0.1", "interface": "lo"},
                            "node1": {"address": "127.0.0.2"},
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            hosts = load_hosts(hosts_path, plan)

        self.assertEqual(set(hosts), {"node0", "node1"})
        self.assertEqual(hosts["node0"].interface, "lo")

    def test_node_template_maps_launcher_index_to_selected_card(self) -> None:
        template = EXAMPLE / "nodes/node0/run_dp_template.sh"
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory)
            fake_vllm = fake_bin / "vllm"
            fake_vllm.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$ASCEND_RT_VISIBLE_DEVICES"\n',
                encoding="utf-8",
            )
            fake_vllm.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "ASCEND_RT_VISIBLE_DEVICES": "4,5,6,7",
                    "RECIPE_MODEL_PATH": "/models/fake",
                    "RECIPE_SERVED_MODEL_NAME": "fake",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(template),
                    "1",
                    "7101",
                    "2",
                    "1",
                    "127.0.0.1",
                    "12321",
                    "1",
                ],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            )

        self.assertEqual(result.stdout.strip(), "5")

    def test_deepseek_v4_plan_is_an_explicit_two_node_a2_reduced_topology(self) -> None:
        plan = load_plan(DEEPSEEK_V4_EXAMPLE / "plan.yaml")
        prefill_run = (DEEPSEEK_V4_EXAMPLE / plan.nodes[0].launch).read_text(
            encoding="utf-8"
        )
        decode_run = (DEEPSEEK_V4_EXAMPLE / plan.nodes[1].launch).read_text(
            encoding="utf-8"
        )
        prefill_template = (
            DEEPSEEK_V4_EXAMPLE / "nodes/node0/run_dp_template.sh"
        ).read_text(encoding="utf-8")
        decode_template = (
            DEEPSEEK_V4_EXAMPLE / "nodes/node1/run_dp_template.sh"
        ).read_text(encoding="utf-8")
        gateway = (DEEPSEEK_V4_EXAMPLE / "gateway/run.sh").read_text(
            encoding="utf-8"
        )

        self.assertEqual([node.id for node in plan.nodes], ["node0", "node1"])
        self.assertEqual([node.role for node in plan.nodes], ["prefill", "decode"])
        self.assertEqual(plan.name, "deepseek-v4-flash-a2-pd-reduced")
        self.assertEqual(
            plan.model.cache_path,
            "vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp",
        )
        self.assertEqual([node.readiness.count for node in plan.nodes], [8, 8])
        for argument in ("--dp-size 8", "--tp-size 1", "--dp-size-local 8"):
            self.assertIn(argument, prefill_run)
        for argument in ("--dp-size 8", "--tp-size 1", "--dp-size-local 8"):
            self.assertIn(argument, decode_run)
        self.assertIn('"kv_role": "kv_producer"', prefill_template)
        self.assertIn('"kv_port": "30000"', prefill_template)
        self.assertIn('"kv_role": "kv_consumer"', decode_template)
        self.assertIn('"kv_port": "30100"', decode_template)
        self.assertIn('"prefill": {"dp_size": 8, "tp_size": 1}', prefill_template)
        self.assertIn('"decode": {"dp_size": 8, "tp_size": 1}', decode_template)
        self.assertEqual(gateway.count('"$RECIPE_NODE_0_IP"'), 8)
        self.assertEqual(gateway.count('"$RECIPE_NODE_1_IP"'), 8)
        self.assertNotIn("RECIPE_NODE_2_IP", gateway)

    def test_qwen35_plan_keeps_tp2_and_scales_dp_to_four_on_a2(self) -> None:
        plan = load_plan(QWEN35_EXAMPLE / "plan.yaml")
        prefill_run = (QWEN35_EXAMPLE / plan.nodes[0].launch).read_text(
            encoding="utf-8"
        )
        decode_run = (QWEN35_EXAMPLE / plan.nodes[1].launch).read_text(
            encoding="utf-8"
        )
        prefill_template = (
            QWEN35_EXAMPLE / "nodes/node0/run_dp_template.sh"
        ).read_text(encoding="utf-8")
        decode_template = (
            QWEN35_EXAMPLE / "nodes/node1/run_dp_template.sh"
        ).read_text(encoding="utf-8")
        gateway = (QWEN35_EXAMPLE / "gateway/run.sh").read_text(encoding="utf-8")

        self.assertEqual([node.id for node in plan.nodes], ["node0", "node1"])
        self.assertEqual([node.role for node in plan.nodes], ["prefill", "decode"])
        self.assertEqual([node.readiness.count for node in plan.nodes], [4, 4])
        self.assertEqual(
            plan.model.cache_path,
            "Eco-Tech/Qwen3.5-27B-w8a8-mtp",
        )
        for launch in (prefill_run, decode_run):
            self.assertIn("--dp-size 4", launch)
            self.assertIn("--tp-size 2", launch)
            self.assertIn("--dp-size-local 4", launch)
        self.assertIn('"kv_role": "kv_producer"', prefill_template)
        self.assertIn('"kv_role": "kv_consumer"', decode_template)
        for template in (prefill_template, decode_template):
            self.assertIn('"prefill": {"dp_size": 4, "tp_size": 2}', template)
            self.assertIn('"decode": {"dp_size": 4, "tp_size": 2}', template)
            self.assertIn('"method":"qwen3_5_mtp"', template)
        self.assertEqual(gateway.count('"$RECIPE_NODE_0_IP"'), 4)
        self.assertEqual(gateway.count('"$RECIPE_NODE_1_IP"'), 4)
        self.assertNotIn("RECIPE_NODE_2_IP", gateway)


class LocalRunnerTests(unittest.TestCase):
    def test_two_nodes_run_every_check_and_evaluation_declared_by_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_dir = Path(directory)
            control_port = free_port()
            prefill_port = free_port()
            decode_port = free_port()
            gateway_port = free_port()
            self._write_fake_runtime(plan_dir)
            self._write_fake_plan(plan_dir, prefill_port, decode_port, gateway_port)

            artifact_root = plan_dir / "artifacts"
            command = ["bash", str(ROOT / "scripts/recipe_ci/run.sh")]
            common_environment = os.environ.copy()
            common_environment.update(
                {
                    "RECIPE_CI_PLAN": str(plan_dir / "plan.yaml"),
                    "RECIPE_CI_CLUSTER_IPS": "127.0.0.1,127.0.0.1",
                    "RECIPE_CI_INTERFACE": "lo",
                    "VLLM_ASCEND_ROOT": str(plan_dir / "vllm-ascend"),
                    "RECIPE_CI_CONTROL_PORT": str(control_port),
                    "RECIPE_CI_STARTUP_TIMEOUT_SECONDS": "20",
                    "RECIPE_CI_RUN_TIMEOUT_SECONDS": "20",
                    "RECIPE_CI_ARTIFACT_ROOT": str(artifact_root),
                }
            )
            leader_environment = common_environment | {"LWS_WORKER_INDEX": "0"}
            worker_environment = common_environment | {"LWS_WORKER_INDEX": "1"}
            leader = subprocess.Popen(
                command,
                env=leader_environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            worker = subprocess.Popen(
                command,
                env=worker_environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            leader_output, _ = leader.communicate(timeout=30)
            worker_output, _ = worker.communicate(timeout=30)
            worker_log = (
                artifact_root / "local-runner-test/node1/service.log"
            ).read_text(encoding="utf-8")

            self.assertEqual(
                leader.returncode,
                0,
                f"{leader_output}\nworker output:\n{worker_output}\nworker log:\n{worker_log}",
            )
            self.assertEqual(worker.returncode, 0, worker_output)
            self.assertIn("local service ready", leader_output)
            self.assertIn("starting gateway", leader_output)
            self.assertIn("plan completed", leader_output)
            self.assertIn("plan completed", worker_output)
            leader_artifacts = artifact_root / "local-runner-test" / "node0"
            self.assertTrue((leader_artifacts / "checks/health.log").is_file())
            self.assertEqual(
                (leader_artifacts / "accuracy/result.txt").read_text(encoding="utf-8"),
                f"127.0.0.1:{gateway_port}\n",
            )
            final_result = json.loads(
                (artifact_root / "local-runner-test/result.json").read_text(
                    encoding="utf-8"
                )
            )
            leader_result = json.loads(
                (leader_artifacts / "node-result.json").read_text(encoding="utf-8")
            )
            worker_result = json.loads(
                (
                    artifact_root
                    / "local-runner-test/node1/node-result.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(final_result["status"], "passed")
            self.assertEqual(
                final_result["evaluations"]["accuracy"]["accuracy"]["metrics"][
                    "accuracy"
                ],
                1.0,
            )
            self.assertEqual(
                final_result["evaluations"]["performance"]["performance"][
                    "metrics"
                ]["request_per_second"],
                2.0,
            )
            self.assertEqual(leader_result["status"], "passed")
            self.assertEqual(worker_result["status"], "passed")
            self.assertIsNotNone(leader_result["cleaned_at"])
            self.assertIsNotNone(worker_result["cleaned_at"])

    def test_remote_service_failure_interrupts_a_supervised_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_dir = Path(directory)
            control_port = free_port()
            prefill_port = free_port()
            decode_port = free_port()
            gateway_port = free_port()
            self._write_fake_runtime(plan_dir)
            self._write_fake_plan(plan_dir, prefill_port, decode_port, gateway_port)
            (plan_dir / "checks/health.sh").write_text(
                "sleep 20\n", encoding="utf-8"
            )
            (plan_dir / "nodes/node1/run.sh").write_text(
                'python3 "$RECIPE_PLAN_DIR/fake_service.py" '
                '"$RECIPE_LOCAL_IP" "$RECIPE_SERVICE_PORT_START" &\n'
                "service_pid=$!\n"
                "sleep 3\n"
                'kill "$service_pid"\n'
                'wait "$service_pid"\n',
                encoding="utf-8",
            )
            hosts_path = plan_dir / "hosts.yaml"
            hosts_path.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "hosts": {
                            "node0": {"address": "127.0.0.1", "interface": "lo"},
                            "node1": {"address": "127.0.0.1", "interface": "lo"},
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            artifact_root = plan_dir / "artifacts"
            command = [
                sys.executable,
                str(ROOT / "scripts/recipe_ci/runner.py"),
                "--plan",
                str(plan_dir / "plan.yaml"),
                "--hosts",
                str(hosts_path),
                "--control-port",
                str(control_port),
                "--startup-timeout-seconds",
                "15",
                "--run-timeout-seconds",
                "15",
                "--artifact-root",
                str(artifact_root),
            ]
            started = time.monotonic()
            leader = subprocess.Popen(
                [*command, "--node-id", "node0"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            worker = subprocess.Popen(
                [*command, "--node-id", "node1"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            leader_output, _ = leader.communicate(timeout=25)
            worker_output, _ = worker.communicate(timeout=25)

            self.assertLess(time.monotonic() - started, 15)
            self.assertNotEqual(leader.returncode, 0, leader_output)
            self.assertNotEqual(worker.returncode, 0, worker_output)
            final_result = json.loads(
                (artifact_root / "local-runner-test/result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(final_result["status"], "failed")
            self.assertEqual(final_result["failure"]["category"], "node_failed")
            self.assertIn("node1", final_result["failure"]["message"])
            self.assertTrue(
                (artifact_root / "local-runner-test/node0/node-result.json").is_file()
            )
            self.assertTrue(
                (artifact_root / "local-runner-test/node1/node-result.json").is_file()
            )

    @staticmethod
    def _write_fake_runtime(plan_dir: Path) -> None:
        required = (
            "examples/external_online_dp/launch_online_dp.py",
        )
        for relative_path in required:
            path = plan_dir / "vllm-ascend" / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fake runtime tool\n", encoding="utf-8")

    @staticmethod
    def _write_fake_plan(
        plan_dir: Path,
        prefill_port: int,
        decode_port: int,
        gateway_port: int,
    ) -> None:
        for directory in (
            "nodes/node0",
            "nodes/node1",
            "gateway",
            "checks",
            "evaluations",
        ):
            (plan_dir / directory).mkdir(parents=True)

        (plan_dir / "fake_service.py").write_text(
            """from http.server import BaseHTTPRequestHandler, HTTPServer
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

HTTPServer((sys.argv[1], int(sys.argv[2])), Handler).serve_forever()
""",
            encoding="utf-8",
        )
        service_script = (
            'exec python3 "$RECIPE_PLAN_DIR/fake_service.py" '
            '"$RECIPE_LOCAL_IP" "$RECIPE_SERVICE_PORT_START"\n'
        )
        (plan_dir / "nodes/node0/run.sh").write_text(service_script, encoding="utf-8")
        (plan_dir / "nodes/node1/run.sh").write_text(service_script, encoding="utf-8")
        (plan_dir / "gateway/run.sh").write_text(
            'exec python3 "$RECIPE_PLAN_DIR/fake_service.py" '
            '"$RECIPE_LOCAL_IP" "$RECIPE_GATEWAY_PORT"\n',
            encoding="utf-8",
        )
        (plan_dir / "checks/health.sh").write_text(
            "python3 -c 'import os, urllib.request; "
            'urllib.request.urlopen(os.environ["RECIPE_ENDPOINT"] + "/healthcheck")\'\n',
            encoding="utf-8",
        )
        (plan_dir / "evaluations/accuracy.sh").write_text(
            'echo "$RECIPE_ENDPOINT_HOST:$RECIPE_ENDPOINT_PORT" '
            '> "$RECIPE_ARTIFACT_DIR/result.txt"\n'
            "printf '%s\\n' '{\"status\": \"passed\", \"type\": \"accuracy\", "
            "\"metrics\": {\"accuracy\": 1.0}}' > \"$RECIPE_STEP_RESULT_FILE\"\n",
            encoding="utf-8",
        )
        (plan_dir / "evaluations/performance.sh").write_text(
            "printf '%s\\n' '{\"status\": \"passed\", "
            "\"type\": \"performance\", \"metrics\": "
            "{\"request_per_second\": 2.0}}' > \"$RECIPE_STEP_RESULT_FILE\"\n",
            encoding="utf-8",
        )

        plan_data = {
            "api_version": "recipe-ci/v1",
            "kind": "MultiNodePlan",
            "metadata": {"name": "local-runner-test"},
            "model": {
                "id": "fake/model",
                "cache_path": "fake/model",
                "served_name": "fake",
            },
            "resources": {"npu_per_node": 1},
            "nodes": [
                {
                    "id": "node0",
                    "role": "prefill",
                    "launch": "nodes/node0/run.sh",
                    "readiness": {"port_start": prefill_port},
                },
                {
                    "id": "node1",
                    "role": "decode",
                    "launch": "nodes/node1/run.sh",
                    "readiness": {"port_start": decode_port},
                },
            ],
            "gateway": {"launch": "gateway/run.sh", "port": gateway_port},
            "checks": [
                {"id": "health", "script": "checks/health.sh", "timeout_seconds": 5}
            ],
            "evaluations": {
                "accuracy": [
                    {
                        "id": "accuracy",
                        "script": "evaluations/accuracy.sh",
                        "timeout_seconds": 5,
                    }
                ],
                "performance": [
                    {
                        "id": "performance",
                        "script": "evaluations/performance.sh",
                        "timeout_seconds": 5,
                    }
                ],
            },
        }
        (plan_dir / "plan.yaml").write_text(
            yaml.safe_dump(plan_data, sort_keys=False), encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
