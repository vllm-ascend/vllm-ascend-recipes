from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.recipe_ci.plan import (  # noqa: E402
    PlanError,
    format_topology_summary,
    load_hosts,
    load_plan,
)


class StrictPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.plan_directory = self.root / "plan"
        self.plan_directory.mkdir()
        for relative_path in (
            "nodes/node0/run.sh",
            "nodes/node1/run.sh",
            "gateway/run.sh",
            "checks/completion.sh",
            "evaluations/accuracy.sh",
            "evaluations/performance.sh",
        ):
            path = self.plan_directory / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        self.plan_data: dict[str, Any] = {
            "api_version": "recipe-ci/v1",
            "kind": "MultiNodePlan",
            "metadata": {"name": "phase2-plan"},
            "model": {
                "id": "example/model",
                "cache_path": "example/model",
                "served_name": "example",
            },
            "resources": {"npu_per_node": 2},
            "nodes": [
                {
                    "id": "node0",
                    "role": "prefill",
                    "launch": "nodes/node0/run.sh",
                    "readiness": {
                        "port_start": 7100,
                        "count": 2,
                        "health_path": "/health",
                    },
                },
                {
                    "id": "node1",
                    "role": "decode",
                    "launch": "nodes/node1/run.sh",
                    "readiness": {"port_start": 7200},
                },
            ],
            "gateway": {
                "launch": "gateway/run.sh",
                "port": 38085,
                "health_path": "/healthcheck",
            },
            "checks": [
                {
                    "id": "completion",
                    "script": "checks/completion.sh",
                    "timeout_seconds": 300,
                }
            ],
            "evaluations": {
                "accuracy": [
                    {
                        "id": "accuracy",
                        "script": "evaluations/accuracy.sh",
                        "timeout_seconds": 600,
                    }
                ],
                "performance": [
                    {
                        "id": "performance",
                        "script": "evaluations/performance.sh",
                        "timeout_seconds": 900,
                    }
                ],
            },
        }
        self.hosts_data: dict[str, Any] = {
            "version": 1,
            "hosts": {
                "node0": {"address": "192.0.2.10", "interface": "eth0"},
                "node1": {"address": "192.0.2.11"},
            },
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_plan(self, data: dict[str, Any] | None = None) -> Path:
        path = self.plan_directory / "plan.yaml"
        path.write_text(
            yaml.safe_dump(
                self.plan_data if data is None else data,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def write_hosts(self, data: dict[str, Any] | None = None) -> Path:
        path = self.plan_directory / "hosts.yaml"
        path.write_text(
            yaml.safe_dump(
                self.hosts_data if data is None else data,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def nested(data: Any, path: tuple[str | int, ...]) -> Any:
        for part in path:
            data = data[part]
        return data

    def test_nodes_are_positionally_named_and_require_roles(self) -> None:
        for invalid_id in ("node2", " node1", "worker"):
            with self.subTest(node_id=invalid_id):
                data = copy.deepcopy(self.plan_data)
                data["nodes"][1]["id"] = invalid_id
                with self.assertRaisesRegex(
                    PlanError, r"nodes\[1\]\.id must be node1"
                ):
                    load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        del data["nodes"][1]["role"]
        with self.assertRaisesRegex(PlanError, r"nodes\[1\]\.role"):
            load_plan(self.write_plan(data))

    def test_at_least_two_nodes_are_required(self) -> None:
        data = copy.deepcopy(self.plan_data)
        data["nodes"] = data["nodes"][:1]

        with self.assertRaisesRegex(PlanError, "at least two"):
            load_plan(self.write_plan(data))

    def test_model_cache_path_is_relative(self) -> None:
        plan = load_plan(self.write_plan())
        self.assertEqual(plan.model.cache_path, "example/model")

        for cache_path in ("/models/example", "../example"):
            with self.subTest(cache_path=cache_path):
                data = copy.deepcopy(self.plan_data)
                data["model"]["cache_path"] = cache_path
                with self.assertRaisesRegex(PlanError, r"model\.cache_path"):
                    load_plan(self.write_plan(data))

    def test_duplicate_roles_are_valid(self) -> None:
        data = copy.deepcopy(self.plan_data)
        data["nodes"][0]["role"] = "decode"
        data["nodes"][1]["role"] = "decode"

        plan = load_plan(self.write_plan(data))

        self.assertEqual([node.role for node in plan.nodes], ["decode", "decode"])

    def test_npu_per_node_is_required_and_positive(self) -> None:
        plan = load_plan(self.write_plan())
        self.assertEqual(plan.resources.npu_per_node, 2)

        for value in (0, True, None):
            with self.subTest(npu_per_node=value):
                data = copy.deepcopy(self.plan_data)
                data["resources"]["npu_per_node"] = value
                with self.assertRaisesRegex(PlanError, r"resources\.npu_per_node"):
                    load_plan(self.write_plan(data))

    def test_safe_slugs_and_stage_local_step_ids(self) -> None:
        cases = (
            (("metadata",), "name", "bad/name", r"metadata\.name"),
            (("checks", 0), "id", "bad check", r"checks\[0\]\.id"),
            (
                ("evaluations", "accuracy", 0),
                "id",
                "bad/accuracy",
                r"evaluations\.accuracy\[0\]\.id",
            ),
        )
        for path, field, value, error_field in cases:
            with self.subTest(path=path):
                data = copy.deepcopy(self.plan_data)
                self.nested(data, path)[field] = value
                with self.assertRaisesRegex(PlanError, error_field):
                    load_plan(self.write_plan(data))

        for path in (("checks",), ("evaluations", "accuracy")):
            with self.subTest(duplicate_stage=path):
                data = copy.deepcopy(self.plan_data)
                steps = self.nested(data, path)
                steps.append(copy.deepcopy(steps[0]))
                with self.assertRaisesRegex(PlanError, "duplicate step id"):
                    load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["checks"][0]["id"] = "shared"
        data["evaluations"]["accuracy"][0]["id"] = "shared"
        data["evaluations"]["performance"][0]["id"] = "shared"
        load_plan(self.write_plan(data))

    def test_unknown_plan_fields_are_rejected_at_each_v1_layer(self) -> None:
        cases = (
            ((), r"plan has unknown fields"),
            (("metadata",), r"metadata has unknown fields"),
            (("model",), r"model has unknown fields"),
            (("resources",), r"resources has unknown fields"),
            (("nodes", 0), r"nodes\[0\] has unknown fields"),
            (
                ("nodes", 0, "readiness"),
                r"nodes\[0\]\.readiness has unknown fields",
            ),
            (("gateway",), r"gateway has unknown fields"),
            (("checks", 0), r"checks\[0\] has unknown fields"),
            (("evaluations",), r"evaluations has unknown fields"),
        )
        for path, message in cases:
            with self.subTest(path=path):
                data = copy.deepcopy(self.plan_data)
                self.nested(data, path)["typo"] = True
                with self.assertRaisesRegex(PlanError, message):
                    load_plan(self.write_plan(data))

    def test_plan_references_cannot_escape_the_plan_directory(self) -> None:
        outside = self.root / "outside.sh"
        outside.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        cases = (
            (("nodes", 0), "launch", r"nodes\[0\]\.launch"),
            (("gateway",), "launch", r"gateway\.launch"),
            (("checks", 0), "script", r"checks\[0\]\.script"),
            (
                ("evaluations", "accuracy", 0),
                "script",
                r"evaluations\.accuracy\[0\]\.script",
            ),
        )
        for path, field, message in cases:
            with self.subTest(path=path):
                data = copy.deepcopy(self.plan_data)
                self.nested(data, path)[field] = "../outside.sh"
                with self.assertRaisesRegex(PlanError, message + ".*outside.sh"):
                    load_plan(self.write_plan(data))

    def test_symlink_targets_cannot_escape_or_alias_node_launches(self) -> None:
        outside = self.root / "outside.sh"
        outside.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (self.plan_directory / "escape.sh").symlink_to(outside)
        data = copy.deepcopy(self.plan_data)
        data["nodes"][0]["launch"] = "escape.sh"

        with self.assertRaisesRegex(
            PlanError, r"nodes\[0\]\.launch.*inside the plan directory"
        ):
            load_plan(self.write_plan(data))

        alias = self.plan_directory / "node1-alias.sh"
        alias.symlink_to(self.plan_directory / "nodes/node0/run.sh")
        data = copy.deepcopy(self.plan_data)
        data["nodes"][1]["launch"] = alias.name
        with self.assertRaisesRegex(PlanError, "each node must have its own"):
            load_plan(self.write_plan(data))

    def test_plan_references_must_be_regular_files(self) -> None:
        data = copy.deepcopy(self.plan_data)
        data["gateway"]["launch"] = "gateway"

        with self.assertRaisesRegex(PlanError, r"gateway\.launch.*regular file"):
            load_plan(self.write_plan(data))

    def test_readiness_ports_counts_and_health_paths_are_bounded(self) -> None:
        for value in (0, 65536, True):
            with self.subTest(port_start=value):
                data = copy.deepcopy(self.plan_data)
                data["nodes"][0]["readiness"]["port_start"] = value
                with self.assertRaisesRegex(
                    PlanError, r"nodes\[0\]\.readiness\.port_start"
                ):
                    load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["nodes"][0]["readiness"]["count"] = 1025
        with self.assertRaisesRegex(PlanError, "count must be at most 1024"):
            load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["nodes"][0]["readiness"]["count"] = 0
        with self.assertRaisesRegex(PlanError, "count must be a positive integer"):
            load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["nodes"][0]["readiness"].update({"port_start": 65535, "count": 2})
        with self.assertRaisesRegex(PlanError, "port range exceeds 65535"):
            load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["nodes"][0]["readiness"]["health_path"] = "health"
        with self.assertRaisesRegex(PlanError, "health_path must start"):
            load_plan(self.write_plan(data))

    def test_gateway_port_health_and_leader_conflicts_are_rejected(self) -> None:
        data = copy.deepcopy(self.plan_data)
        data["gateway"]["port"] = 0
        with self.assertRaisesRegex(PlanError, "gateway.port must be between"):
            load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["gateway"]["health_path"] = "healthcheck"
        with self.assertRaisesRegex(PlanError, "gateway.health_path must start"):
            load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["gateway"]["port"] = 7101
        with self.assertRaisesRegex(PlanError, "conflicts with leader readiness"):
            load_plan(self.write_plan(data))

        data = copy.deepcopy(self.plan_data)
        data["gateway"]["port"] = 7200
        load_plan(self.write_plan(data))

    def test_gateway_is_optional_only_when_the_leader_has_readiness(self) -> None:
        data = copy.deepcopy(self.plan_data)
        del data["gateway"]
        del data["nodes"][0]["readiness"]

        with self.assertRaisesRegex(PlanError, "leader needs HTTP readiness"):
            load_plan(self.write_plan(data))

    def test_hosts_must_exactly_match_nodes_and_use_ipv4(self) -> None:
        plan = load_plan(self.write_plan())

        missing = copy.deepcopy(self.hosts_data)
        del missing["hosts"]["node1"]
        with self.assertRaisesRegex(PlanError, r"missing=\['node1'\]"):
            load_hosts(self.write_hosts(missing), plan)

        extra = copy.deepcopy(self.hosts_data)
        extra["hosts"]["worker"] = {"address": "192.0.2.12"}
        with self.assertRaisesRegex(PlanError, r"unexpected=\['worker'\]"):
            load_hosts(self.write_hosts(extra), plan)

        for address in ("2001:db8::1", "node0.example.test"):
            with self.subTest(address=address):
                data = copy.deepcopy(self.hosts_data)
                data["hosts"]["node0"]["address"] = address
                with self.assertRaisesRegex(PlanError, "must be an IPv4 address"):
                    load_hosts(self.write_hosts(data), plan)

    def test_unknown_hosts_fields_are_rejected(self) -> None:
        plan = load_plan(self.write_plan())
        data = copy.deepcopy(self.hosts_data)
        data["typo"] = True
        with self.assertRaisesRegex(PlanError, "hosts file has unknown fields"):
            load_hosts(self.write_hosts(data), plan)

        data = copy.deepcopy(self.hosts_data)
        data["hosts"]["node0"]["typo"] = True
        with self.assertRaisesRegex(
            PlanError, "hosts.node0 has unknown fields"
        ):
            load_hosts(self.write_hosts(data), plan)

        data = copy.deepcopy(self.hosts_data)
        data["version"] = 1.0
        with self.assertRaisesRegex(PlanError, "hosts version must be 1"):
            load_hosts(self.write_hosts(data), plan)

    def test_topology_summary_formats_static_and_host_details(self) -> None:
        plan = load_plan(self.write_plan())
        static_summary = format_topology_summary(plan)

        self.assertIn("Plan: phase2-plan", static_summary)
        self.assertIn("API version: recipe-ci/v1", static_summary)
        self.assertIn("Leader: node0", static_summary)
        self.assertIn("NPUs per node: 2", static_summary)
        self.assertIn(
            "node0 role=prefill launch=nodes/node0/run.sh readiness=7100-7101",
            static_summary,
        )
        self.assertIn(
            "node1 role=decode launch=nodes/node1/run.sh readiness=7200",
            static_summary,
        )
        self.assertIn("leader=node0 launch=gateway/run.sh port=38085", static_summary)
        self.assertIn("completion timeout=300s", static_summary)
        self.assertIn("accuracy: accuracy timeout=600s", static_summary)
        self.assertNotIn("Endpoint:", static_summary)

        hosts = load_hosts(self.write_hosts(), plan)
        hosts_summary = format_topology_summary(plan, hosts)
        self.assertIn("address=192.0.2.10 interface=eth0", hosts_summary)
        self.assertIn("address=192.0.2.11 interface=auto", hosts_summary)
        self.assertIn("Endpoint: http://192.0.2.10:38085", hosts_summary)

    def test_topology_summary_uses_leader_readiness_without_gateway(self) -> None:
        data = copy.deepcopy(self.plan_data)
        del data["gateway"]
        plan = load_plan(self.write_plan(data))
        hosts = load_hosts(self.write_hosts(), plan)

        summary = format_topology_summary(plan, hosts)

        self.assertIn("Gateway:\n  none", summary)
        self.assertIn("Endpoint: http://192.0.2.10:7100", summary)


if __name__ == "__main__":
    unittest.main()
