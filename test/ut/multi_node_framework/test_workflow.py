from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
ENTRY_WORKFLOW = ROOT / ".github/workflows/verify_multi_node.yaml"
REUSABLE_WORKFLOW = ROOT / ".github/workflows/_verify_multi_node.yaml"
TARGET_REGISTRY = ROOT / ".github/verification-targets.yaml"
LWS_TEMPLATE = ROOT / "test/recipe/multi_node/scripts/k8s/lws.yaml.tmpl"
LWS_RENDERER = ROOT / "test/recipe/multi_node/scripts/k8s/render_lws.py"
LWS_ADAPTER = ROOT / "test/recipe/multi_node/scripts/k8s/run_lws.sh"
RUN_SCRIPT = ROOT / "test/recipe/multi_node/scripts/run.sh"
GENERATED_PD_PLAN = (
    ROOT / "test/recipe/multi_node/.generated/template-pd/pd-2n2c/plan.yaml"
)


def setUpModule() -> None:
    """Generate the ignored plan fixture used by the run-script smoke test."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "test/recipe/multi_node/convert.py"),
            "--recipe",
            str(ROOT / "models/en/DeepSeek/template_pd.yaml"),
            "--test-id",
            "pd-2n2c",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode:
        raise RuntimeError(result.stdout)


def tearDownModule() -> None:
    """Remove only the generated plan fixture created by this test module."""
    shutil.rmtree(GENERATED_PD_PLAN.parent, ignore_errors=True)
    for directory in (GENERATED_PD_PLAN.parents[1], GENERATED_PD_PLAN.parents[2]):
        try:
            directory.rmdir()
        except OSError:
            pass


def workflow(path: Path) -> dict[str, object]:
    """Load a workflow without YAML 1.1 coercing the ``on`` key to boolean."""
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def render_lws() -> dict[str, object]:
    """Render a representative four-node LWS and return its YAML object."""
    values = {
        "lws_name": "multi-node-case-123-1",
        "namespace": "vllm-project",
        "image": "example.invalid/vllm-ascend:test-a2",
        "run_root": "/root/.cache/multi-node/123-1",
        "plan": "test/recipe/multi_node/.generated/template-pd/pd-2n2c/plan.yaml",
        "node_count": "4",
        "npu_per_node": "2",
        "startup_timeout_seconds": "1800",
        "run_timeout_seconds": "7200",
        "pvc_name": "multi-node-pvc",
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        values_path = root / "values.json"
        output_path = root / "lws.yaml"
        values_path.write_text(json.dumps(values), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(LWS_RENDERER),
                "--template",
                str(LWS_TEMPLATE),
                "--values",
                str(values_path),
                "--output",
                str(output_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode:
            raise AssertionError(result.stdout)
        return yaml.safe_load(output_path.read_text(encoding="utf-8"))


class MultiNodeWorkflowTests(unittest.TestCase):
    def test_entry_workflow_declares_only_case_identity(self) -> None:
        value = workflow(ENTRY_WORKFLOW)

        self.assertEqual(
            set(value["on"]), {"schedule", "pull_request", "workflow_dispatch"}
        )
        # One reusable-workflow job per case; each filters itself on the
        # prepare output that matches its recipe file.
        expected = {
            "verify-template-pd": {
                "name": "deepseek-v2-lite-pd-2n2c",
                "recipe": "models/en/DeepSeek/template_pd.yaml",
                "test_id": "pd-2n2c",
                "target_id": "template-deepseek-v2-lite-pd-a2-w8a8-1p1d",
                "flag": "pd_template",
            },
            "verify-v2lite": {
                "name": "deepseek-v2-lite-recipe-pd-2n2c",
                "recipe": "models/en/DeepSeek/DeepSeek-V2-Lite-W8A8.yaml",
                "test_id": "dsv2lite-pd-2n2c",
                "target_id": "deepseek-v2-lite-a2-w8a8-1p1d",
                "flag": "v2lite",
            },
            "verify-qwen": {
                "name": "qwen3-30b-a3b-dp-2n2c",
                "recipe": "models/en/Qwen/template2_non_pd.yaml",
                "test_id": "dp-2n2c",
                "target_id": "template-qwen3-30b-a3b-a2-bf16-2node",
                "flag": "qwen",
            },
            "verify-qwen30b": {
                "name": "qwen3-30b-a3b-recipe-dp-2n2c",
                "recipe": "models/en/Qwen/Qwen3-30B-A3B.yaml",
                "test_id": "qwen30b-dp-2n2c",
                "target_id": "qwen3-30b-a3b-a2-bf16-2node",
                "flag": "qwen30b",
            },
        }
        for job_id, case in expected.items():
            job = value["jobs"][job_id]
            self.assertEqual(job["uses"], "./.github/workflows/_verify_multi_node.yaml")
            self.assertEqual(job["needs"], "prepare")
            self.assertEqual(
                job["with"],
                {
                    "name": case["name"],
                    "target_id": case["target_id"],
                    "recipe": case["recipe"],
                    "test_id": case["test_id"],
                },
            )
            self.assertIn(f"needs.prepare.outputs.{case['flag']} == 'true'", job["if"])
            self.assertIn("github.event_name == 'schedule'", job["if"])

        registry = yaml.safe_load(TARGET_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                target["id"]
                for target in registry["targets"]
                if target["mode"] == "multi-node"
            },
            {case["target_id"] for case in expected.values()},
        )

        finalizer = value["jobs"]["finalize-target-results"]
        self.assertEqual(
            set(finalizer["needs"]),
            {"prepare", *expected},
        )
        self.assertEqual(finalizer["if"], "always()")
        finalizer_steps = {step["name"]: step for step in finalizer["steps"]}
        self.assertIn("Write fallback verification results", finalizer_steps)
        fallback_artifact = finalizer_steps["Upload fallback verification results"]
        self.assertIn("verification-target-results-", fallback_artifact["with"]["name"])
        self.assertEqual(
            finalizer_steps["Write fallback verification results"]["env"],
            {
                "TEMPLATE_PD_RESULT": "${{ needs.verify-template-pd.result }}",
                "V2LITE_RESULT": "${{ needs.verify-v2lite.result }}",
                "QWEN_RESULT": "${{ needs.verify-qwen.result }}",
                "QWEN30B_RESULT": "${{ needs.verify-qwen30b.result }}",
            },
        )


        # prepare reports which multi-node recipe YAMLs changed (en or zh).
        prepare = value["jobs"]["prepare"]
        self.assertEqual(
            prepare["outputs"],
            {
                "pd_template": "${{ steps.select.outputs.pd_template }}",
                "v2lite": "${{ steps.select.outputs.v2lite }}",
                "qwen": "${{ steps.select.outputs.qwen }}",
                "qwen30b": "${{ steps.select.outputs.qwen30b }}",
            },
        )
        self.assertEqual(
            job["uses"], "./.github/workflows/_verify_multi_node.yaml"
        )

    def test_reusable_workflow_converts_runs_uploads_and_cleans(self) -> None:
        value = workflow(REUSABLE_WORKFLOW)
        text = REUSABLE_WORKFLOW.read_text(encoding="utf-8")
        job = value["jobs"]["run"]
        steps = {step["name"]: step for step in job["steps"]}

        self.assertEqual(
            set(value["on"]["workflow_call"]["inputs"]),
            {"name", "target_id", "recipe", "test_id"},
        )
        self.assertEqual(
            value["concurrency"]["group"],
            "multi-node-a2b4-k8s",
        )
        self.assertEqual(job["timeout-minutes"], "180")
        self.assertEqual(job["env"]["MULTI_NODE_UPLOAD_K8S_DIAGNOSTICS"], "false")
        self.assertEqual(
            job["env"]["MULTI_NODE_UPLOAD_FULL_DIAGNOSTICS_ON_SUCCESS"], "false"
        )

        ordered_steps = [step["name"] for step in job["steps"]]
        self.assertLess(
            ordered_steps.index("Convert multi-node template"),
            ordered_steps.index("Prepare run identity and stage source"),
        )
        self.assertLess(
            ordered_steps.index("Build artifact bundle"),
            ordered_steps.index("Remove staged PVC data"),
        )
        self.assertIn("test/recipe/multi_node/convert.py", text)
        self.assertIn("len(plan.nodes)", text)
        self.assertIn("plan.resources.npu_per_node", text)
        self.assertEqual(text.count('kubectl wait "${pod_resources[@]}"'), 2)
        self.assertIn('kubectl logs -f "$pod"', text)
        self.assertIn('pod-status/node${index}.exit', text)
        self.assertIn('kubectl delete leaderworkerset "$LWS_NAME"', text)

        github_upload = steps["Upload Multi-node framework bundle to GitHub"]
        self.assertIn("steps.upload_obs.outcome != 'success'", github_upload["if"])
        self.assertEqual(
            steps["Remove staged PVC data"]["if"],
            "always() && steps.prepare.outcome == 'success'",
        )
        self.assertIn(
            'rm -rf -- "$MULTI_NODE_RUN_ROOT"',
            steps["Remove staged PVC data"]["run"],
        )
        target_upload = steps["Upload configuration verification result"]
        self.assertIn("verification-target-results-", target_upload["with"]["name"])
        self.assertIn("${TARGET_ID}.json", steps["Write configuration verification result"]["run"])
        self.assertTrue(target_upload["continue-on-error"])
        self.assertEqual(target_upload["with"]["compression-level"], "0")
        self.assertIn("Upload configuration verification result (retry)", steps)
        self.assertIn("steps.upload_result.outcome != 'success'", steps["Upload configuration verification result (retry)"]["if"])

        bundle = steps["Build artifact bundle"]
        self.assertIn("FRAMEWORK_OUTCOME", bundle["env"])
        self.assertIn("status-only", bundle["run"])
        self.assertIn('FRAMEWORK_OUTCOME" != "success', bundle["run"])
        self.assertIn("tar -czf", bundle["run"])
        self.assertIn("compression-level", github_upload["with"])
        self.assertEqual(github_upload["with"]["compression-level"], "0")
        self.assertIn("Upload Multi-node framework bundle to GitHub (retry)", steps)
        self.assertIn("steps.upload_bundle.outcome != 'success'", steps["Upload Multi-node framework bundle to GitHub (retry)"]["if"])

    def test_lws_runtime_contract_and_intra_case_node_isolation(self) -> None:
        lws = render_lws()
        template = lws["spec"]["leaderWorkerTemplate"]
        leader_template = template["leaderTemplate"]
        worker_template = template["workerTemplate"]
        leader = leader_template["spec"]["containers"][0]
        worker = worker_template["spec"]["containers"][0]

        self.assertEqual(template["size"], 4)
        self.assertTrue(leader_template["spec"]["hostNetwork"])
        self.assertTrue(worker_template["spec"]["hostNetwork"])
        self.assertEqual(leader["command"], worker["command"])
        self.assertIn(
            "/test/recipe/multi_node/scripts/k8s/run_lws.sh",
            leader["command"][2],
        )
        self.assertEqual(
            leader["resources"]["limits"]["huawei.com/ascend-1980"], 2
        )
        self.assertTrue(leader["securityContext"]["privileged"])

        for pod_template in (leader_template, worker_template):
            self.assertEqual(
                pod_template["metadata"]["labels"]["multi-node-run"],
                "multi-node-case-123-1",
            )
        anti_affinity = leader_template["spec"]["affinity"]["podAntiAffinity"]
        placement = anti_affinity[
            "requiredDuringSchedulingIgnoredDuringExecution"
        ][0]
        self.assertEqual(
            placement["labelSelector"]["matchLabels"],
            {"multi-node-run": "multi-node-case-123-1"},
        )
        self.assertEqual(placement["topologyKey"], "kubernetes.io/hostname")

        volumes = {
            item["name"]: item for item in leader_template["spec"]["volumes"]
        }
        self.assertEqual(
            volumes["shared-volume"]["persistentVolumeClaim"]["claimName"],
            "multi-node-pvc",
        )
        env = {item["name"]: item["value"] for item in leader["env"]}
        self.assertEqual(env["MULTI_NODE_NODE_COUNT"], "4")
        self.assertEqual(env["MULTI_NODE_RUN_ROOT"], "/root/.cache/multi-node/123-1")
        self.assertEqual(
            env["AIS_BENCH_ENVIRONMENT_IDENTITY"],
            "runtime=example.invalid/vllm-ascend:test-a2",
        )

    def test_lws_renderer_rejects_missing_and_unexpected_values(self) -> None:
        sys.path.insert(0, str(LWS_RENDERER.parent))
        try:
            from render_lws import render_template
        finally:
            sys.path.pop(0)

        template = (
            "apiVersion: leaderworkerset.x-k8s.io/v1\n"
            "kind: LeaderWorkerSet\n"
            "metadata:\n"
            "  name: {{ name }}\n"
        )
        with self.assertRaisesRegex(ValueError, "missing LWS template values: name"):
            render_template(template, {})
        with self.assertRaisesRegex(
            ValueError, "unexpected LWS template values: extra"
        ):
            render_template(template, {"name": "multi-node", "extra": "value"})

    def test_lws_aisbench_install_failure_is_broadcast_to_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            fake_bin = run_root / "bin"
            fake_bin.mkdir()
            timeout = fake_bin / "timeout"
            timeout.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" != "--foreground" ]] || shift
[[ $# -gt 0 ]] || exit 2
shift
exec "$@"
""",
                encoding="utf-8",
            )
            timeout.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "LWS_LEADER_ADDRESS": "leader.group.namespace.svc",
                    "MULTI_NODE_NODE_COUNT": "2",
                    "MULTI_NODE_RUN_ROOT": str(run_root),
                    "MULTI_NODE_STARTUP_TIMEOUT_SECONDS": "30",
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                }
            )
            environment.pop("AIS_BENCH_ENVIRONMENT_IDENTITY", None)
            environment.pop("MULTI_NODE_VALIDATE_ONLY", None)

            leader = subprocess.run(
                ["bash", str(LWS_ADAPTER)],
                env=environment | {"LWS_WORKER_INDEX": "0"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            failure_file = run_root / "aisbench.env.failed"
            self.assertEqual(leader.returncode, 1, leader.stdout)
            self.assertIn(
                "AISBench preparation failed on node0 with exit code 1",
                failure_file.read_text(encoding="utf-8"),
            )

            worker = subprocess.run(
                ["bash", str(LWS_ADAPTER)],
                env=environment | {"LWS_WORKER_INDEX": "1"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(worker.returncode, 1, worker.stdout)
            self.assertIn("AISBench preparation failed on node0", worker.stdout)

    def test_lws_adapter_translates_dns_and_worker_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory)
            getent = fake_bin / "getent"
            getent.write_text(
                "#!/usr/bin/env bash\n"
                "case \"$2\" in\n"
                "  recipe-case-0.group.namespace.svc.cluster.local) address=10.0.0.1 ;;\n"
                "  recipe-case-0-1.group.namespace) address=10.0.0.2 ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n"
                "printf '%s STREAM host\\n' \"$address\"\n",
                encoding="utf-8",
            )
            getent.chmod(0o755)
            python = fake_bin / "python3"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'node=%s ips=%s\\n' \"$MULTI_NODE_NODE_INDEX\" "
                '"$MULTI_NODE_CLUSTER_IPS"\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "LWS_LEADER_ADDRESS": (
                        "recipe-case-0.group.namespace.svc.cluster.local"
                    ),
                    "LWS_WORKER_INDEX": "1",
                    "MULTI_NODE_NODE_COUNT": "2",
                    "MULTI_NODE_PLAN": "unused-by-fake-python.yaml",
                    "MULTI_NODE_VALIDATE_ONLY": "true",
                }
            )
            result = subprocess.run(
                ["bash", str(LWS_ADAPTER)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("node=1 ips=10.0.0.1,10.0.0.2", result.stdout)

    def test_common_run_script_can_validate_without_cluster_or_npu(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "MULTI_NODE_PLAN": GENERATED_PD_PLAN.relative_to(ROOT).as_posix(),
                "MULTI_NODE_VALIDATE_ONLY": "true",
                "PATH": f"{Path(sys.executable).parent}:{environment['PATH']}",
            }
        )
        result = subprocess.run(
            ["bash", str(RUN_SCRIPT)],
            cwd=ROOT,
            env=environment,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIn("Plan: template-pd-pd-2n2c", result.stdout)


if __name__ == "__main__":
    unittest.main()
