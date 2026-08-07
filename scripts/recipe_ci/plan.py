#!/usr/bin/env python3
"""Data model for the hand-written Recipe CI intermediate plan."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


API_VERSION = "recipe-ci/v1"
MAX_READINESS_COUNT = 1024
SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class PlanError(ValueError):
    """The plan or local hosts file cannot be executed."""


@dataclass(frozen=True)
class Model:
    id: str
    cache_path: str
    served_name: str


@dataclass(frozen=True)
class Resources:
    npu_per_node: int


@dataclass(frozen=True)
class Readiness:
    port_start: int
    count: int = 1
    health_path: str = "/health"


@dataclass(frozen=True)
class Node:
    id: str
    index: int
    role: str
    launch: str
    readiness: Readiness | None


@dataclass(frozen=True)
class Gateway:
    launch: str
    port: int
    health_path: str = "/healthcheck"


@dataclass(frozen=True)
class ScriptStep:
    id: str
    script: str
    timeout_seconds: int


@dataclass(frozen=True)
class Evaluations:
    accuracy: list[ScriptStep]
    performance: list[ScriptStep]


@dataclass(frozen=True)
class Plan:
    path: Path
    name: str
    model: Model
    resources: Resources
    nodes: list[Node]
    gateway: Gateway | None
    checks: list[ScriptStep]
    evaluations: Evaluations

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def leader(self) -> Node:
        return self.nodes[0]

    @property
    def api_version(self) -> str:
        return API_VERSION

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise PlanError(f"Unknown node: {node_id}")


@dataclass(frozen=True)
class Host:
    address: str
    interface: str | None = None


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanError(f"{field} must be a mapping")
    return value


def _check_fields(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = [key for key in value if key not in allowed]
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise PlanError(f"{field} has unknown fields: {names}")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{field} must be a non-empty string")
    return value


def _slug(value: Any, field: str) -> str:
    slug = _string(value, field)
    if SAFE_SLUG.fullmatch(slug) is None:
        raise PlanError(
            f"{field} must match [A-Za-z0-9][A-Za-z0-9._-]*, got {slug}"
        )
    return slug


def _relative_path(value: Any, field: str) -> str:
    relative_path = PurePosixPath(_string(value, field))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise PlanError(f"{field} must be a relative path, got {relative_path}")
    return relative_path.as_posix()


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PlanError(f"{field} must be a positive integer")
    return value


def _port(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 65535
    ):
        raise PlanError(f"{field} must be between 1 and 65535, got {value}")
    return value


def _health_path(value: Any, field: str) -> str:
    health_path = _string(value, field)
    if not health_path.startswith("/"):
        raise PlanError(f"{field} must start with '/', got {health_path}")
    return health_path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PlanError(f"File not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise PlanError(f"Invalid YAML in {path}: {error}") from error
    return _mapping(value, str(path))


def _script(path: Path, value: Any, field: str) -> str:
    script = _string(value, field)
    try:
        resolved = (path.parent / script).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PlanError(
            f"{field} must reference an existing file, got {script}"
        ) from error
    try:
        resolved.relative_to(path.parent)
    except ValueError as error:
        raise PlanError(
            f"{field} must stay inside the plan directory, got {script}"
        ) from error
    if not resolved.is_file():
        raise PlanError(f"{field} must reference a regular file, got {script}")
    return script


def _steps(path: Path, value: Any, field: str) -> list[ScriptStep]:
    if not isinstance(value, list):
        raise PlanError(f"{field} must be a list")

    steps: list[ScriptStep] = []
    step_ids: set[str] = set()
    for position, item in enumerate(value):
        item_field = f"{field}[{position}]"
        item_raw = _mapping(item, item_field)
        _check_fields(item_raw, {"id", "script", "timeout_seconds"}, item_field)
        step_id = _slug(item_raw.get("id"), f"{item_field}.id")
        if step_id in step_ids:
            raise PlanError(f"{field} has duplicate step id: {step_id}")
        steps.append(
            ScriptStep(
                id=step_id,
                script=_script(
                    path,
                    item_raw.get("script"),
                    f"{item_field}.script",
                ),
                timeout_seconds=_positive_int(
                    item_raw.get("timeout_seconds", 300),
                    f"{item_field}.timeout_seconds",
                ),
            )
        )
        step_ids.add(step_id)
    return steps


def load_plan(path: Path) -> Plan:
    """Load the first intermediate format, without interpreting Recipe YAML."""
    path = path.resolve()
    raw = _read_yaml(path)
    _check_fields(
        raw,
        {
            "api_version",
            "kind",
            "metadata",
            "model",
            "resources",
            "nodes",
            "gateway",
            "checks",
            "evaluations",
        },
        "plan",
    )
    if raw.get("api_version") != API_VERSION:
        raise PlanError(f"api_version must be {API_VERSION}")
    if raw.get("kind") != "MultiNodePlan":
        raise PlanError("kind must be MultiNodePlan")

    metadata = _mapping(raw.get("metadata"), "metadata")
    _check_fields(metadata, {"name"}, "metadata")
    model_raw = _mapping(raw.get("model"), "model")
    _check_fields(model_raw, {"id", "cache_path", "served_name"}, "model")
    resources_raw = _mapping(raw.get("resources"), "resources")
    _check_fields(resources_raw, {"npu_per_node"}, "resources")
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or len(nodes_raw) < 2:
        raise PlanError("nodes must contain at least two entries")

    nodes: list[Node] = []
    launch_scripts: set[Path] = set()
    for index, item in enumerate(nodes_raw):
        node_field = f"nodes[{index}]"
        node_raw = _mapping(item, node_field)
        _check_fields(node_raw, {"id", "role", "launch", "readiness"}, node_field)
        node_id = _string(node_raw.get("id"), f"{node_field}.id")
        expected_node_id = f"node{index}"
        if node_id != expected_node_id:
            raise PlanError(
                f"{node_field}.id must be {expected_node_id}, got {node_id}"
            )
        role = _string(node_raw.get("role"), f"{node_field}.role")
        launch = _script(path, node_raw.get("launch"), f"{node_field}.launch")
        launch_path = (path.parent / launch).resolve()
        readiness_value = node_raw.get("readiness")
        readiness: Readiness | None = None
        if readiness_value is not None:
            readiness_field = f"{node_field}.readiness"
            readiness_raw = _mapping(readiness_value, readiness_field)
            _check_fields(
                readiness_raw,
                {"port_start", "count", "health_path"},
                readiness_field,
            )
            port_start = _port(
                readiness_raw.get("port_start"),
                f"{readiness_field}.port_start",
            )
            count = _positive_int(
                readiness_raw.get("count", 1),
                f"{readiness_field}.count",
            )
            if count > MAX_READINESS_COUNT:
                raise PlanError(
                    f"{readiness_field}.count must be at most "
                    f"{MAX_READINESS_COUNT}, got {count}"
                )
            if port_start + count - 1 > 65535:
                raise PlanError(
                    f"{readiness_field} port range exceeds 65535: "
                    f"{port_start}-{port_start + count - 1}"
                )
            readiness = Readiness(
                port_start=port_start,
                count=count,
                health_path=_health_path(
                    readiness_raw.get("health_path", "/health"),
                    f"{readiness_field}.health_path",
                ),
            )
        if launch_path in launch_scripts:
            raise PlanError(f"each node must have its own launch script: {launch}")
        nodes.append(
            Node(
                id=node_id,
                index=index,
                role=role,
                launch=launch,
                readiness=readiness,
            )
        )
        launch_scripts.add(launch_path)

    gateway: Gateway | None = None
    gateway_raw = raw.get("gateway")
    if gateway_raw is not None:
        gateway_mapping = _mapping(gateway_raw, "gateway")
        _check_fields(
            gateway_mapping, {"launch", "port", "health_path"}, "gateway"
        )
        gateway = Gateway(
            launch=_script(path, gateway_mapping.get("launch"), "gateway.launch"),
            port=_port(gateway_mapping.get("port"), "gateway.port"),
            health_path=_health_path(
                gateway_mapping.get("health_path", "/healthcheck"),
                "gateway.health_path",
            ),
        )
        leader_readiness = nodes[0].readiness
        if (
            leader_readiness is not None
            and leader_readiness.port_start
            <= gateway.port
            < leader_readiness.port_start + leader_readiness.count
        ):
            raise PlanError(
                "gateway.port conflicts with leader readiness ports: "
                f"{gateway.port}"
            )
    elif nodes[0].readiness is None:
        raise PlanError("the leader needs HTTP readiness when gateway is omitted")

    evaluations_raw = _mapping(raw.get("evaluations", {}), "evaluations")
    _check_fields(evaluations_raw, {"accuracy", "performance"}, "evaluations")
    return Plan(
        path=path,
        name=_slug(metadata.get("name"), "metadata.name"),
        model=Model(
            id=_string(model_raw.get("id"), "model.id"),
            cache_path=_relative_path(
                model_raw.get("cache_path"), "model.cache_path"
            ),
            served_name=_string(model_raw.get("served_name"), "model.served_name"),
        ),
        resources=Resources(
            npu_per_node=_positive_int(
                resources_raw.get("npu_per_node"), "resources.npu_per_node"
            )
        ),
        nodes=nodes,
        gateway=gateway,
        checks=_steps(path, raw.get("checks", []), "checks"),
        evaluations=Evaluations(
            accuracy=_steps(
                path, evaluations_raw.get("accuracy", []), "evaluations.accuracy"
            ),
            performance=_steps(
                path,
                evaluations_raw.get("performance", []),
                "evaluations.performance",
            ),
        ),
    )


def load_hosts(path: Path, plan: Plan) -> dict[str, Host]:
    raw = _read_yaml(path.resolve())
    _check_fields(raw, {"version", "hosts"}, "hosts file")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise PlanError("hosts version must be 1")
    hosts_raw = _mapping(raw.get("hosts"), "hosts")
    expected = {node.id for node in plan.nodes}
    if set(hosts_raw) != expected:
        missing = sorted(expected - set(hosts_raw))
        unexpected = sorted(str(key) for key in set(hosts_raw) - expected)
        raise PlanError(
            "hosts keys must match plan nodes; "
            f"missing={missing}, unexpected={unexpected}"
        )

    hosts: dict[str, Host] = {}
    for node_id, value in hosts_raw.items():
        host_raw = _mapping(value, f"hosts.{node_id}")
        _check_fields(host_raw, {"address", "interface"}, f"hosts.{node_id}")
        interface = host_raw.get("interface")
        if interface is not None:
            interface = _string(interface, f"hosts.{node_id}.interface")
        address = _string(host_raw.get("address"), f"hosts.{node_id}.address")
        try:
            ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError as error:
            raise PlanError(
                f"hosts.{node_id}.address must be an IPv4 address, got {address}"
            ) from error
        hosts[node_id] = Host(
            address=address,
            interface=interface,
        )
    return hosts


def format_topology_summary(
    plan: Plan, hosts: dict[str, Host] | None = None
) -> str:
    """Format the validated static topology, with local hosts when provided."""
    lines = [
        f"Plan: {plan.name}",
        f"API version: {plan.api_version}",
        f"Leader: {plan.leader.id}",
        f"Model: {plan.model.id}",
        f"Served name: {plan.model.served_name}",
        f"NPUs per node: {plan.resources.npu_per_node}",
        "",
        "Nodes:",
    ]
    for node in plan.nodes:
        if node.readiness:
            last_port = node.readiness.port_start + node.readiness.count - 1
            if node.readiness.count == 1:
                readiness = str(node.readiness.port_start)
            else:
                readiness = f"{node.readiness.port_start}-{last_port}"
        else:
            readiness = "none"
        details = (
            f"  {node.id} role={node.role} launch={node.launch} "
            f"readiness={readiness}"
        )
        if hosts is not None:
            host = hosts[node.id]
            details += (
                f" address={host.address} interface={host.interface or 'auto'}"
            )
        lines.append(details)

    lines.extend(["", "Gateway:"])
    if plan.gateway:
        lines.append(
            f"  leader={plan.leader.id} launch={plan.gateway.launch} "
            f"port={plan.gateway.port} health={plan.gateway.health_path}"
        )
        endpoint_port = plan.gateway.port
    else:
        lines.append("  none")
        leader_readiness = plan.leader.readiness
        if leader_readiness is None:
            raise PlanError("leader endpoint has no readiness configuration")
        endpoint_port = leader_readiness.port_start

    if hosts is not None:
        lines.extend(
            [
                "",
                f"Endpoint: http://{hosts[plan.leader.id].address}:{endpoint_port}",
            ]
        )

    lines.extend(["", "Checks:"])
    if plan.checks:
        lines.extend(
            f"  {step.id} timeout={step.timeout_seconds}s" for step in plan.checks
        )
    else:
        lines.append("  none")

    lines.extend(["", "Evaluations:"])
    evaluation_steps = (
        ("accuracy", plan.evaluations.accuracy),
        ("performance", plan.evaluations.performance),
    )
    found_evaluation = False
    for stage, steps in evaluation_steps:
        for step in steps:
            lines.append(f"  {stage}: {step.id} timeout={step.timeout_seconds}s")
            found_evaluation = True
    if not found_evaluation:
        lines.append("  none")
    return "\n".join(lines)
