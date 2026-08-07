#!/usr/bin/env python3
"""Execute one node from a Recipe CI multi-node intermediate plan."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.recipe_ci.coordinator import (  # noqa: E402
    CoordinatorClient,
    CoordinatorError,
    LeaderCoordinator,
)
from scripts.recipe_ci.plan import (  # noqa: E402
    Host,
    Node,
    Plan,
    PlanError,
    ScriptStep,
    format_topology_summary,
    load_hosts,
    load_plan,
)
from scripts.recipe_ci.process import (  # noqa: E402
    CancellationRequested,
    ManagedProcess,
    ManagedProcessExited,
    check_processes,
    signal_cancellation_event,
    start_process,
    stop_processes,
    tail_log,
    wait_for_process,
)
from scripts.recipe_ci.result import (  # noqa: E402
    RunFailure,
    build_final_result,
    build_node_result,
    process_record,
    read_json,
    utc_now,
    write_json_atomic,
)


DEFAULT_VLLM_ASCEND_ROOT = Path("/vllm-workspace/vllm-ascend")
MODEL_CACHE_ROOT = Path("/root/.cache/modelscope/hub/models")
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class RunnerError(RuntimeError):
    """The local node could not complete the plan."""


class StageFailure(RuntimeError):
    """Carry the single structured failure shape through the linear lifecycle."""

    def __init__(self, failure: RunFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--hosts", type=Path)
    parser.add_argument("--node-id")
    parser.add_argument("--vllm-ascend-root", type=Path)
    parser.add_argument("--control-port", type=int, default=29599)
    parser.add_argument("--startup-timeout-seconds", type=int, default=1800)
    parser.add_argument("--run-timeout-seconds", type=int, default=3600)
    parser.add_argument("--artifact-root", type=Path, default=Path("/tmp/recipe-ci"))
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def interface_addresses() -> dict[str, str]:
    """Return Linux interface-to-IPv4 mappings used for local node selection."""
    addresses: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    else:
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 4:
                addresses[fields[1]] = fields[3].split("/", 1)[0]
        if addresses:
            return addresses

    # Minimal runtime images may not contain iproute2. SIOCGIFADDR keeps local
    # and hostNetwork execution usable without adding another image dependency.
    try:
        import fcntl
        import struct

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            for _, interface in socket.if_nameindex():
                try:
                    request = struct.pack("256s", interface[:15].encode())
                    response = fcntl.ioctl(probe.fileno(), 0x8915, request)
                except OSError:
                    continue
                addresses[interface] = socket.inet_ntoa(response[20:24])
    except (ImportError, OSError):
        pass
    return addresses


def select_node(plan: Plan, hosts: dict[str, Host], requested: str | None) -> Node:
    if requested:
        return plan.node(requested)

    local_addresses = set(interface_addresses().values())
    local_addresses.update(
        address[4][0]
        for address in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    )
    matches = [node for node in plan.nodes if hosts[node.id].address in local_addresses]
    if len(matches) != 1:
        raise RunnerError("cannot select one local node; pass --node-id explicitly")
    return matches[0]


def select_interface(host: Host) -> str:
    if host.interface:
        return host.interface
    for interface, address in interface_addresses().items():
        if address == host.address:
            return interface
    raise RunnerError("cannot detect the local interface; set it in hosts.yaml")


def resolve_vllm_ascend_root(requested: Path | None) -> Path:
    root = requested or Path(
        os.environ.get("VLLM_ASCEND_ROOT", str(DEFAULT_VLLM_ASCEND_ROOT))
    )
    # The runner exposes this runtime contract but does not require every plan to
    # consume the upstream source tree. A plan that uses it fails at its own script.
    return root.expanduser().resolve()


def base_environment(
    plan: Plan,
    node: Node,
    hosts: dict[str, Host],
    interface: str,
    model_path: str,
    vllm_ascend_root: Path,
    control_port: int,
    plan_artifact_directory: Path,
    node_artifact_directory: Path,
) -> dict[str, str]:
    local_ip = hosts[node.id].address
    leader_ip = hosts[plan.leader.id].address
    environment = os.environ.copy()
    environment.update(
        {
            "RECIPE_PLAN_DIR": str(plan.directory),
            "RECIPE_REPOSITORY_ROOT": str(ROOT),
            "RECIPE_NODE_ID": node.id,
            "RECIPE_NODE_INDEX": str(node.index),
            "RECIPE_NODE_ROLE": node.role,
            "RECIPE_LOCAL_IP": local_ip,
            "RECIPE_LOCAL_INTERFACE": interface,
            "RECIPE_LEADER_IP": leader_ip,
            "RECIPE_CONTROL_PORT": str(control_port),
            "RECIPE_MODEL_ID": plan.model.id,
            "RECIPE_MODEL_PATH": model_path,
            "RECIPE_SERVED_MODEL_NAME": plan.model.served_name,
            "RECIPE_VLLM_ASCEND_ROOT": str(vllm_ascend_root),
            "RECIPE_ARTIFACT_ROOT": str(plan_artifact_directory),
            "RECIPE_NODE_ARTIFACT_DIR": str(node_artifact_directory),
            "HCCL_IF_IP": local_ip,
            "HCCL_SOCKET_IFNAME": interface,
            "GLOO_SOCKET_IFNAME": interface,
            "TP_SOCKET_IFNAME": interface,
        }
    )
    if node.readiness:
        environment["RECIPE_SERVICE_PORT_START"] = str(node.readiness.port_start)
        environment["RECIPE_SERVICE_COUNT"] = str(node.readiness.count)
    if plan.gateway:
        environment["RECIPE_GATEWAY_PORT"] = str(plan.gateway.port)
    for plan_node in plan.nodes:
        environment[f"RECIPE_NODE_{plan_node.index}_IP"] = hosts[
            plan_node.id
        ].address

    no_proxy = environment.get("NO_PROXY", environment.get("no_proxy", "")).split(
        ","
    )
    no_proxy.extend(host.address for host in hosts.values())
    environment["NO_PROXY"] = ",".join(
        dict.fromkeys(item for item in no_proxy if item)
    )
    environment["no_proxy"] = environment["NO_PROXY"]
    return environment


def wait_http_ready(
    url: str,
    timeout: int,
    check_runtime: Callable[[], None],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_runtime()
        try:
            with DIRECT_OPENER.open(url, timeout=2) as response:
                if response.status < 400:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"timed out waiting for {url}")


def wait_node_ready(
    node: Node,
    host: Host,
    timeout: int,
    check_runtime: Callable[[], None],
) -> None:
    if node.readiness is None:
        return
    deadline = time.monotonic() + timeout
    for offset in range(node.readiness.count):
        remaining = max(1, int(deadline - time.monotonic()))
        url = (
            f"http://{host.address}:{node.readiness.port_start + offset}"
            f"{node.readiness.health_path}"
        )
        wait_http_ready(url, remaining, check_runtime)


def run_steps(
    stage: str,
    steps: list[ScriptStep],
    plan: Plan,
    environment: dict[str, str],
    artifact_directory: Path,
    managed_processes: list[ManagedProcess],
    check_runtime: Callable[[], None],
    cancellation,
) -> dict[str, object]:
    results: dict[str, object] = {}
    for step in steps:
        stage_directory = artifact_directory / stage
        step_directory = stage_directory / step.id
        step_directory.mkdir(parents=True, exist_ok=True)
        result_path = step_directory / "result.json"
        step_environment = environment.copy()
        step_environment.update(
            {
                # v1 compatibility: existing plans use the stage directory.
                "RECIPE_ARTIFACT_DIR": str(stage_directory),
                "RECIPE_STEP_ARTIFACT_DIR": str(step_directory),
                "RECIPE_STEP_RESULT_FILE": str(result_path),
            }
        )
        script = plan.directory / step.script
        log_path = stage_directory / f"{step.id}.log"
        print(f"[leader] running {stage}: {step.id}; log: {log_path}")
        item = start_process(
            f"{stage} {step.id}",
            ["bash", script.name],
            cwd=script.parent,
            environment=step_environment,
            log_path=log_path,
            stage=stage,
            node_id=plan.leader.id,
        )
        managed_processes.append(item)
        try:
            return_code = wait_for_process(
                item,
                step.timeout_seconds,
                check_runtime=check_runtime,
                cancellation=cancellation,
            )
        except subprocess.TimeoutExpired as error:
            category = "check_failed" if stage == "checks" else "evaluation_failed"
            raise StageFailure(
                RunFailure(
                    category=category,
                    stage=stage,
                    node_id=plan.leader.id,
                    step_id=step.id,
                    message=f"{stage} {step.id} timed out after {step.timeout_seconds}s",
                    log_path=log_path.relative_to(artifact_directory.parent).as_posix(),
                )
            ) from error
        if return_code != 0:
            category = "check_failed" if stage == "checks" else "evaluation_failed"
            message = f"{stage} {step.id} exited with {return_code}"
            log_tail = tail_log(log_path)
            if log_tail:
                message += f"\nlast log lines:\n{log_tail}"
            raise StageFailure(
                RunFailure(
                    category=category,
                    stage=stage,
                    node_id=plan.leader.id,
                    step_id=step.id,
                    message=message,
                    return_code=return_code,
                    log_path=log_path.relative_to(artifact_directory.parent).as_posix(),
                )
            )

        result: dict[str, object] = {"status": "passed"}
        if result_path.exists():
            try:
                result = read_json(result_path)
            except (OSError, ValueError) as error:
                raise StageFailure(
                    RunFailure(
                        category="evaluation_failed",
                        stage=stage,
                        node_id=plan.leader.id,
                        step_id=step.id,
                        message=f"invalid step result {result_path}: {error}",
                        log_path=log_path.relative_to(
                            artifact_directory.parent
                        ).as_posix(),
                    )
                ) from error
            if result.get("status") != "passed":
                category = (
                    "check_failed" if stage == "checks" else "evaluation_failed"
                )
                raise StageFailure(
                    RunFailure(
                        category=category,
                        stage=stage,
                        node_id=plan.leader.id,
                        step_id=step.id,
                        message=(
                            f"{stage} {step.id} reported status "
                            f"{result.get('status')!r}"
                        ),
                        log_path=log_path.relative_to(
                            artifact_directory.parent
                        ).as_posix(),
                    )
                )
        elif stage != "checks":
            raise StageFailure(
                RunFailure(
                    category="evaluation_failed",
                    stage=stage,
                    node_id=plan.leader.id,
                    step_id=step.id,
                    message=f"evaluation did not write {result_path}",
                    log_path=log_path.relative_to(artifact_directory.parent).as_posix(),
                )
            )
        results[step.id] = result
    return results


def write_environment(path: Path, plan: Plan, node: Node) -> None:
    packages: dict[str, str] = {}
    for distribution in ("vllm", "vllm-ascend", "ais-bench-benchmark"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    value = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "packages": packages,
        "plan": plan.name,
        "node_id": node.id,
    }
    for name in ("RECIPE_CI_IMAGE", "GITHUB_SHA"):
        if os.environ.get(name):
            value[name.lower()] = os.environ[name]
    write_json_atomic(path, value)


def run_node(
    plan: Plan,
    hosts: dict[str, Host],
    node: Node,
    args: argparse.Namespace,
) -> None:
    host = hosts[node.id]
    interface = select_interface(host)
    model_path = str(MODEL_CACHE_ROOT / plan.model.cache_path)
    plan_artifact_directory = (args.artifact_root / plan.name).resolve()
    artifact_directory = plan_artifact_directory / node.id
    artifact_directory.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    environment = base_environment(
        plan,
        node,
        hosts,
        interface,
        model_path,
        resolve_vllm_ascend_root(args.vllm_ascend_root),
        args.control_port,
        plan_artifact_directory,
        artifact_directory,
    )
    endpoint_port = (
        plan.gateway.port
        if plan.gateway
        else plan.leader.readiness.port_start  # validated by load_plan
    )
    endpoint_host = hosts[plan.leader.id].address
    environment.update(
        {
            "RECIPE_ENDPOINT_HOST": endpoint_host,
            "RECIPE_ENDPOINT_PORT": str(endpoint_port),
            "RECIPE_ENDPOINT": f"http://{endpoint_host}:{endpoint_port}",
        }
    )

    coordinator: LeaderCoordinator | None = None
    client = CoordinatorClient(hosts[plan.leader.id].address, args.control_port)
    managed_processes: list[ManagedProcess] = []
    runtime_processes: list[ManagedProcess] = []
    primary_failure: RunFailure | None = None
    cleanup_failures: list[RunFailure] = []
    warnings: list[str] = []
    ready_at: str | None = None
    terminal_at: str | None = None
    terminal_status = "failed"
    check_results: dict[str, object] = {}
    evaluation_results: dict[str, object] = {}

    with signal_cancellation_event() as cancellation:
        try:
            def check_cancellation() -> None:
                if cancellation.is_set():
                    raise CancellationRequested("cancellation requested")

            if node.id == plan.leader.id:
                coordinator = LeaderCoordinator(
                    [item.id for item in plan.nodes],
                    args.control_port,
                    artifact_directory / "coordinator.log",
                )
                coordinator.start()
            else:
                print(f"[{node.id}] waiting for the leader coordinator")
                client.wait_available(
                    args.startup_timeout_seconds, check_cancellation
                )

            print(
                f"[{node.id}] starting service launcher; "
                f"log: {artifact_directory / 'service.log'}"
            )
            service_process = start_process(
                "service launcher",
                ["bash", (plan.directory / node.launch).name],
                cwd=(plan.directory / node.launch).parent,
                environment=environment,
                log_path=artifact_directory / "service.log",
                stage="service",
                node_id=node.id,
            )
            managed_processes.append(service_process)
            runtime_processes.append(service_process)

            def check_local_runtime() -> None:
                check_cancellation()
                check_processes(runtime_processes)

            try:
                wait_node_ready(
                    node, host, args.startup_timeout_seconds, check_local_runtime
                )
            except TimeoutError as error:
                raise StageFailure(
                    RunFailure(
                        category="startup_timeout",
                        stage="service",
                        node_id=node.id,
                        message=str(error),
                        log_path=f"{node.id}/service.log",
                    )
                ) from error
            ready_at = utc_now()

            if coordinator:
                coordinator.state.mark_ready(node.id)
                print(f"[{node.id}] local service ready; waiting for the other nodes")
                coordinator.wait_ready(
                    args.startup_timeout_seconds, check_local_runtime
                )

                if plan.gateway:
                    gateway_script = plan.directory / plan.gateway.launch
                    print(
                        f"[{node.id}] starting gateway; "
                        f"log: {artifact_directory / 'gateway.log'}"
                    )
                    gateway_process = start_process(
                        "gateway",
                        ["bash", gateway_script.name],
                        cwd=gateway_script.parent,
                        environment=environment,
                        log_path=artifact_directory / "gateway.log",
                        stage="gateway",
                        node_id=node.id,
                    )
                    managed_processes.append(gateway_process)
                    runtime_processes.append(gateway_process)

                    def check_leader_runtime() -> None:
                        check_local_runtime()
                        coordinator.raise_if_failed()

                    try:
                        wait_http_ready(
                            environment["RECIPE_ENDPOINT"]
                            + plan.gateway.health_path,
                            args.startup_timeout_seconds,
                            check_leader_runtime,
                        )
                    except TimeoutError as error:
                        raise StageFailure(
                            RunFailure(
                                category="gateway_failed",
                                stage="gateway",
                                node_id=node.id,
                                message=str(error),
                                log_path=f"{node.id}/gateway.log",
                            )
                        ) from error
                else:

                    def check_leader_runtime() -> None:
                        check_local_runtime()
                        coordinator.raise_if_failed()

                check_results = run_steps(
                    "checks",
                    plan.checks,
                    plan,
                    environment,
                    artifact_directory,
                    managed_processes,
                    check_leader_runtime,
                    cancellation,
                )
                if plan.evaluations.accuracy:
                    evaluation_results["accuracy"] = run_steps(
                        "accuracy",
                        plan.evaluations.accuracy,
                        plan,
                        environment,
                        artifact_directory,
                        managed_processes,
                        check_leader_runtime,
                        cancellation,
                    )
                if plan.evaluations.performance:
                    evaluation_results["performance"] = run_steps(
                        "performance",
                        plan.evaluations.performance,
                        plan,
                        environment,
                        artifact_directory,
                        managed_processes,
                        check_leader_runtime,
                        cancellation,
                    )
                check_leader_runtime()
                coordinator.state.finish("passed")
                terminal_status = "passed"
                terminal_at = utc_now()
            else:
                client.mark_ready(node.id, args.startup_timeout_seconds)
                print(f"[{node.id}] local service ready; waiting for the leader result")
                state = client.wait_terminal(
                    args.run_timeout_seconds, check_local_runtime
                )
                terminal_status = str(state["status"])
                terminal_at = utc_now()
                if terminal_status != "passed":
                    category = (
                        "cancelled" if terminal_status == "cancelled" else "node_failed"
                    )
                    raise StageFailure(
                        RunFailure(
                            category=category,
                            stage="coordination",
                            node_id=node.id,
                            message=str(state.get("message") or terminal_status),
                        )
                    )
        except StageFailure as error:
            primary_failure = error.failure
        except CancellationRequested as error:
            terminal_status = "cancelled"
            primary_failure = RunFailure(
                category="cancelled",
                stage="runner",
                node_id=node.id,
                message=str(error),
            )
        except ManagedProcessExited as error:
            if error.item.stage == "gateway":
                category = "gateway_failed"
            elif ready_at is None:
                category = "launch_failed"
            else:
                category = "node_failed"
            primary_failure = RunFailure(
                category=category,
                stage=error.item.stage or "service",
                node_id=node.id,
                message=str(error),
                return_code=error.return_code,
                log_path=error.item.log_path.relative_to(
                    plan_artifact_directory
                ).as_posix(),
            )
        except CoordinatorError as error:
            category = (
                "coordinator_unreachable"
                if error.code == "coordinator_unreachable"
                else "node_failed"
            )
            primary_failure = RunFailure(
                category=category,
                stage="coordination",
                node_id=node.id,
                message=str(error),
            )
        except (OSError, RunnerError) as error:
            primary_failure = RunFailure(
                category="launch_failed",
                stage="runner",
                node_id=node.id,
                message=str(error),
            )
        except Exception as error:
            primary_failure = RunFailure(
                category="internal_error",
                stage="runner",
                node_id=node.id,
                message=f"{type(error).__name__}: {error}",
            )

        if primary_failure is not None:
            terminal_status = (
                "cancelled"
                if primary_failure.category == "cancelled"
                else "failed"
            )
            terminal_at = terminal_at or utc_now()
            if coordinator:
                try:
                    coordinator.state.finish(terminal_status, primary_failure.message)
                except CoordinatorError as error:
                    warnings.append(f"could not publish terminal status: {error}")
            else:
                if primary_failure.category != "cancelled":
                    try:
                        client.mark_failed(node.id, primary_failure.message)
                    except CoordinatorError as error:
                        warnings.append(f"could not report node failure: {error}")

        # Terminal is published before cleanup, but cleaned is not reported until
        # process groups are stopped, logs are closed, and node-result.json exists.
        for message in stop_processes(managed_processes):
            cleanup_failures.append(
                RunFailure(
                    category="cleanup_failed",
                    stage="cleanup",
                    node_id=node.id,
                    message=message,
                )
            )

        process_results = [
            process_record(
                name=item.name,
                pid=item.pid,
                process_group=item.process_group,
                started_at=item.started_at,
                stage=item.stage,
                return_code=item.process.poll(),
                log_path=item.log_path.relative_to(plan_artifact_directory),
            )
            for item in managed_processes
        ]
        write_environment(artifact_directory / "environment.json", plan, node)
        node_result = build_node_result(
            node_id=node.id,
            role=node.role,
            status=terminal_status,
            started_at=started_at,
            processes=process_results,
            ready_at=ready_at,
            terminal_at=terminal_at,
            primary_failure=primary_failure,
            cleanup_errors=cleanup_failures,
            warnings=warnings,
            artifacts=[
                path.relative_to(plan_artifact_directory)
                for path in sorted(artifact_directory.rglob("*"))
                if path.is_file() and path.name != "node-result.json"
            ],
        )
        write_json_atomic(artifact_directory / "node-result.json", node_result)

        if coordinator:
            coordinator.state.mark_cleaned(node.id)
            try:
                coordinator.wait_cleaned(30)
            except CoordinatorError as error:
                cleanup_failure = RunFailure(
                    category="cleanup_failed",
                    stage="cleanup",
                    node_id=node.id,
                    message=str(error),
                )
                cleanup_failures.append(cleanup_failure)
                if primary_failure is None:
                    primary_failure = cleanup_failure
                    terminal_status = "failed"
            snapshot = coordinator.state.snapshot()
            final_result = build_final_result(
                plan=plan.name,
                status=terminal_status,
                started_at=started_at,
                nodes={
                    node_id: {
                        "status": status,
                        "failure": snapshot["failures"].get(node_id),
                    }
                    for node_id, status in snapshot["nodes"].items()
                },
                checks=check_results,
                evaluations=evaluation_results,
                primary_failure=primary_failure,
                cleanup_errors=cleanup_failures,
                warnings=warnings,
            )
            write_json_atomic(plan_artifact_directory / "result.json", final_result)
            coordinator.close()
        else:
            try:
                client.mark_cleaned(node.id)
            except CoordinatorError as error:
                cleanup_failure = RunFailure(
                    category="cleanup_failed",
                    stage="cleanup",
                    node_id=node.id,
                    message=f"could not report cleaned: {error}",
                )
                cleanup_failures.append(cleanup_failure)
                if primary_failure is None:
                    primary_failure = cleanup_failure
                    terminal_status = "failed"
                node_result = build_node_result(
                    node_id=node.id,
                    role=node.role,
                    status=terminal_status,
                    started_at=started_at,
                    processes=process_results,
                    ready_at=ready_at,
                    terminal_at=terminal_at,
                    primary_failure=primary_failure,
                    cleanup_errors=cleanup_failures,
                    warnings=warnings,
                    artifacts=node_result["artifacts"],
                )
                write_json_atomic(
                    artifact_directory / "node-result.json", node_result
                )

    if primary_failure is not None or cleanup_failures:
        failure = primary_failure or cleanup_failures[0]
        raise RunnerError(f"{failure.category}: {failure.message}")
    print(f"[{node.id}] plan completed")


def main() -> int:
    args = parse_args()
    try:
        plan = load_plan(args.plan)
        hosts = load_hosts(args.hosts, plan) if args.hosts else None
        if args.validate_only:
            print(format_topology_summary(plan, hosts))
            return 0
        if hosts is None:
            raise RunnerError("--hosts is required unless --validate-only is used")
        node = select_node(plan, hosts, args.node_id)
        run_node(plan, hosts, node, args)
        return 0
    except (OSError, PlanError, CoordinatorError, RunnerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
