"""Convert one validated Recipe scenario into an executable bundle plan.

This layer owns topology semantics.  It accepts already-rendered script text,
statically inspects the commands, and emits runtime-neutral scripts without
executing or sourcing any Recipe content.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .model import (
    BundleSpec,
    ConversionError,
    GatewaySpec,
    NodeSpec,
    ReadinessSpec,
    ScenarioSource,
    StageSpec,
    StepSpec,
)
from .shell import (
    ExternalDpCommand,
    GatewayCommand,
    ShellAnalysisError,
    VllmServeCommand,
    export_assignments,
    parse_completion_check,
    parse_external_dp,
    parse_gateway,
    parse_vllm_serve,
)


_PD_CASE = re.compile(r"^(?P<prefill>[1-9]\d*)p(?P<decode>[1-9]\d*)d$")
_PD_CASE_LEGACY = re.compile(
    r"^(?P<prefill>[1-9]\d*)[pP](?P<decode>[1-9]\d*)[dD]"
)
_NON_PD_CASE = re.compile(r"^(?P<nodes>[1-9]\d*)-node$")
_SCRIPT_NAME = re.compile(
    r"^(?P<role>prefill|decode)-(?P<index>\d+)-(?P<kind>template|launch)$"
)
_HEADLESS_NAME = re.compile(r"^headless-(?P<index>\d+)$")
_SHELL_VARIABLE = re.compile(r"^\$\{?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}?$")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

_COMPLETION_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

response=$(curl --fail --silent --show-error \\
    "$MULTI_NODE_ENDPOINT/v1/completions" \\
    -H 'Content-Type: application/json' \\
    -d "{\\\"model\\\":\\\"$MULTI_NODE_SERVED_MODEL_NAME\\\",\\\"prompt\\\":\\\"The future of AI is\\\",\\\"max_tokens\\\":50,\\\"temperature\\\":0}")

python3 -c 'import json, sys; assert json.load(sys.stdin)["choices"]' <<<"$response"
printf '%s\\n' '{"status":"passed"}' > "$MULTI_NODE_STEP_RESULT_FILE"
"""

_AISBENCH_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

exec python3 "$MULTI_NODE_REPOSITORY_ROOT/test/recipe/multi_node/scripts/aisbench.py" \\
    --config "$MULTI_NODE_STEP_INPUT_FILE"
"""


def _fail(message: str) -> ConversionError:
    """Create the single user-facing error type exposed by this layer."""
    return ConversionError(message)


def _deployment_kind(deployment: str) -> str:
    """Classify a deployment into the runtime's pd / non-pd contract.

    The canonical values are "pd" and "non-pd"; legacy recipes carry display
    values such as "Multi-Node PD Separation" (pd) or "多节点-PD分离" (pd).
    """
    d = deployment.strip().lower()
    if d == "pd":
        return "pd"
    if d == "non-pd":
        return "non-pd"
    if "pd" in d:
        return "pd"
    return "non-pd"


def _parse_pd_case(deployment: str, case: str) -> re.Match[str] | None:
    """Parse a PD case into (prefill, decode) node counts.

    Canonical "1p1d" is preferred; legacy display cases such as
    "1P1D (1 Prefill node + 1 Decode node)" are accepted via a prefix match.
    """
    m = _PD_CASE.fullmatch(case)
    if m is not None:
        return m
    if _deployment_kind(deployment) == "pd" and case:
        m = _PD_CASE_LEGACY.match(case)
        if m is not None:
            return m
    return None


def _script(source: ScenarioSource, name: str) -> str:
    """Return one supported non-empty shell script from the scenario."""
    item = source.scripts.get(name)
    if item is None:
        raise _fail(f"scenario {source.test_id}: missing script {name!r}")
    if item.language not in {"bash", "shell", "sh"}:
        raise _fail(f"script {name!r}: language must be bash/shell/sh")
    if not item.content.strip():
        raise _fail(f"script {name!r}: content must not be empty")
    return item.content


def _analyze(name: str, parser: Any, content: str) -> Any:
    """Run a static analyzer and attach the owning Recipe script name."""
    try:
        return parser(content)
    except ShellAnalysisError as exc:
        raise _fail(f"script {name!r}: {exc}") from exc


def _positive(value: int, field: str) -> None:
    """Require a positive integer for a topology quantity."""
    if type(value) is not int or value <= 0:
        raise _fail(f"{field} must be a positive integer")


def _option(command: VllmServeCommand, name: str, script_name: str) -> str:
    """Read a required vLLM option with script-local diagnostics."""
    try:
        return command.require(name)
    except ShellAnalysisError as exc:
        raise _fail(f"script {script_name!r}: {exc}") from exc


def _literal_integer(value: str, field: str) -> int:
    """Decode a positive integer that must be visible during conversion."""
    try:
        result = int(value)
    except ValueError as exc:
        raise _fail(f"{field} must be a literal integer, got {value!r}") from exc
    if result <= 0:
        raise _fail(f"{field} must be positive")
    return result


def _port(value: int, field: str) -> None:
    """Validate the numeric range of one statically extracted TCP port."""
    if value <= 0 or value > 65535:
        raise _fail(f"{field} must be in the range 1..65535")


def _require_variable(value: str, expected: str, field: str) -> None:
    """Require an exact runtime placeholder instead of a literal address."""
    match = _SHELL_VARIABLE.fullmatch(value)
    if match is None or match.group("name") != expected:
        raise _fail(f"{field} must reference ${expected}, got {value!r}")


def _consistent(values: Sequence[str], field: str) -> str:
    """Return the only value shared by every service script."""
    unique = set(values)
    if len(unique) != 1:
        raise _fail(f"all service scripts must use the same {field}: {sorted(unique)}")
    return values[0]


def _replace_option(command: str, option: str, value: str) -> str:
    """Rewrite exactly one converter-owned option in analyzed shell text."""
    pattern = re.compile(
        rf"({re.escape(option)}\s+)(?:\"[^\"]*\"|'[^']*'|\S+)"
    )
    result, count = pattern.subn(rf"\g<1>{value}", command, count=1)
    if count != 1:
        raise _fail(f"cannot rewrite required option {option}")
    return result


def _runtime_vllm_command(
    serve: VllmServeCommand,
    *,
    served_name: bool,
    host: str | None = None,
    port: str | None = None,
    dp_address: str | None = None,
) -> str:
    """Rewrite only converter-owned arguments and preserve model tuning flags."""
    result, count = re.subn(
        rf"(\bvllm\s+serve\s+){re.escape(serve.model_id)}(?=\s)",
        r'\g<1>"$MULTI_NODE_MODEL_PATH"',
        serve.command,
        count=1,
    )
    if count != 1:
        raise _fail("cannot rewrite vllm serve model argument")
    if served_name:
        result = _replace_option(
            result, "--served-model-name", '"$MULTI_NODE_SERVED_MODEL_NAME"'
        )
    if host is not None:
        result = _replace_option(result, "--host", host)
    if port is not None:
        result = _replace_option(result, "--port", port)
    if dp_address is not None:
        result = _replace_option(result, "--data-parallel-address", dp_address)
    return result


def _format_command(command: str, continuation_indent: int = 4) -> str:
    """Keep generated scripts readable while retaining the analyzed tokens."""
    continuation = " " + "\\" + "\n" + " " * continuation_indent + "--"
    return command.replace(" --", continuation) + "\n"


def _exports(script: str) -> str:
    """Retain safe environment exports while normalizing analyzer errors."""
    try:
        retained = export_assignments(script)
    except ShellAnalysisError as exc:
        raise _fail(f"unsafe export assignment: {exc}") from exc
    return "\n".join(retained) + ("\n" if retained else "")


def _pd_template(serve: VllmServeCommand, original: str) -> str:
    """Generate one external-DP rank template with device and log isolation."""
    command = _runtime_vllm_command(serve, served_name=True)
    for option, argument in (
        ("--port", "$2"),
        ("--data-parallel-size", "$3"),
        ("--data-parallel-rank", "$4"),
        ("--data-parallel-address", "$5"),
        ("--data-parallel-rpc-port", "$6"),
        ("--tensor-parallel-size", "$7"),
    ):
        command = _replace_option(command, option, f'"{argument}"')
    return f"""#!/usr/bin/env bash
set -euo pipefail

# The upstream launcher passes a logical device index as $1.  Map it to the
# physical devices selected by the multi-node runner.
IFS=',' read -r -a selected_devices <<< "$MULTI_NODE_VISIBLE_DEVICES"
if (( $1 < 0 || $1 >= ${{#selected_devices[@]}} )); then
    echo "logical device index $1 is outside MULTI_NODE_VISIBLE_DEVICES" >&2
    exit 2
fi
export ASCEND_RT_VISIBLE_DEVICES="${{selected_devices[$1]}}"
{_exports(original)}
rank_log_directory="$MULTI_NODE_NODE_ARTIFACT_DIR/servers"
mkdir -p "$rank_log_directory"
rank_log="$rank_log_directory/rank-$4.log"

{{
    echo "external DP rank=$4 device=$ASCEND_RT_VISIBLE_DEVICES port=$2"
    exec {_format_command(command, continuation_indent=8).rstrip()}
}} > "$rank_log" 2>&1
"""


def _pd_launcher(command: ExternalDpCommand) -> str:
    """Generate a node launcher around the upstream external-DP helper."""
    return f"""#!/usr/bin/env bash
set -euo pipefail

exec python3 "$MULTI_NODE_REPOSITORY_ROOT/test/recipe/multi_node/scripts/run_online_dp.py" \\
    "$MULTI_NODE_VLLM_ASCEND_ROOT/examples/external_online_dp/launch_online_dp.py" \\
    --dp-size {command.dp_size} \\
    --tp-size {command.tp_size} \\
    --dp-size-local "$MULTI_NODE_SERVICE_COUNT" \\
    --dp-rank-start {command.dp_rank_start} \\
    --dp-address "$MULTI_NODE_LOCAL_IP" \\
    --dp-rpc-port {command.dp_rpc_port} \\
    --vllm-start-port "$MULTI_NODE_SERVICE_PORT_START"
"""


def _internal_launcher(
    serve: VllmServeCommand, original: str, *, api: bool
) -> str:
    """Generate an API or headless internal-DP vLLM process."""
    command = _runtime_vllm_command(
        serve,
        served_name=api,
        host='"$MULTI_NODE_LOCAL_IP"' if api else None,
        port='"$MULTI_NODE_SERVICE_PORT_START"' if api else None,
        dp_address='"$MULTI_NODE_NODE_0_IP"',
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail

export ASCEND_RT_VISIBLE_DEVICES="$MULTI_NODE_VISIBLE_DEVICES"
{_exports(original)}
exec {_format_command(command)}"""


def _gateway_script(
    gateway: GatewayCommand,
    role_nodes: Mapping[str, list[tuple[int, ExternalDpCommand]]],
) -> str:
    """Generate the P/D proxy from the fully validated backend topology."""
    def endpoint_arguments(role: str) -> tuple[str, str]:
        """Expand every local DP service into runtime host/port arguments."""
        hosts: list[str] = []
        ports: list[str] = []
        for global_index, launch in role_nodes[role]:
            hosts.extend(
                f'"$MULTI_NODE_NODE_{global_index}_IP"'
                for _ in range(launch.dp_size_local)
            )
            ports.extend(
                str(launch.vllm_start_port + offset)
                for offset in range(launch.dp_size_local)
            )
        return " ".join(hosts), " ".join(ports)

    prefill_hosts, prefill_ports = endpoint_arguments("prefill")
    decode_hosts, decode_ports = endpoint_arguments("decode")
    return f"""#!/usr/bin/env bash
set -euo pipefail

exec python3 \\
    "$MULTI_NODE_VLLM_ASCEND_ROOT/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py" \\
    --host "$MULTI_NODE_LOCAL_IP" \\
    --port "$MULTI_NODE_GATEWAY_PORT" \\
    --prefiller-hosts {prefill_hosts} \\
    --prefiller-ports {prefill_ports} \\
    --decoder-hosts {decode_hosts} \\
    --decoder-ports {decode_ports}
"""


def _validate_pd_template(
    name: str,
    role: str,
    serve: VllmServeCommand,
    launch: ExternalDpCommand,
) -> None:
    """Check positional launcher arguments and Mooncake role/topology data."""
    expected = {
        "--port": "$2",
        "--data-parallel-size": "$3",
        "--data-parallel-rank": "$4",
        "--data-parallel-address": "$5",
        "--data-parallel-rpc-port": "$6",
        "--tensor-parallel-size": "$7",
    }
    for option, value in expected.items():
        if _option(serve, option, name) != value:
            raise _fail(f"script {name!r}: {option} must be {value}")
    kv_text = _option(serve, "--kv-transfer-config", name)
    try:
        kv = __import__("json").loads(kv_text)
    except (ValueError, TypeError) as exc:
        raise _fail(f"script {name!r}: --kv-transfer-config must be literal JSON") from exc
    expected_role = "kv_producer" if role == "prefill" else "kv_consumer"
    if kv.get("kv_connector") != "MooncakeConnectorV1":
        raise _fail(
            f"script {name!r}: PD conversion requires MooncakeConnectorV1"
        )
    if kv.get("kv_role") != expected_role:
        raise _fail(
            f"script {name!r}: {role} requires kv_role={expected_role!r}"
        )
    kv_port = kv.get("kv_port")
    try:
        _port(int(kv_port), f"script {name!r} kv_port")
    except (TypeError, ValueError) as exc:
        raise _fail(f"script {name!r}: kv_port must be an integer") from exc
    extra = kv.get("kv_connector_extra_config")
    if not isinstance(extra, Mapping) or not isinstance(extra.get(role), Mapping):
        raise _fail(
            f"script {name!r}: kv_connector_extra_config requires {role} topology"
        )
    role_topology = extra[role]
    if role_topology.get("dp_size") != launch.dp_size:
        raise _fail(
            f"script {name!r}: KV {role}.dp_size must match launcher dp-size"
        )
    if role_topology.get("tp_size") != launch.tp_size:
        raise _fail(
            f"script {name!r}: KV {role}.tp_size must match launcher tp-size"
        )
    if launch.dp_size_local * launch.tp_size <= 0:
        raise _fail(f"script {name!r}: invalid external-DP device topology")


def _plan_pd(
    source: ScenarioSource,
    counts: Mapping[str, int],
) -> tuple[
    tuple[NodeSpec, ...], GatewaySpec, dict[str, str], list[VllmServeCommand]
]:
    """Expand numbered Prefill/Decode scripts into ordered Runtime nodes."""
    expected_scripts = {"service-check", "gateway-0"}
    grouped: dict[str, dict[int, dict[str, str]]] = {
        "prefill": {},
        "decode": {},
    }
    for name in source.scripts:
        match = _SCRIPT_NAME.fullmatch(name)
        if match:
            role = match.group("role")
            index = int(match.group("index"))
            grouped[role].setdefault(index, {})[match.group("kind")] = name
            expected_scripts.add(name)
    unexpected = set(source.scripts) - expected_scripts
    if unexpected:
        raise _fail(f"PD scenario has unsupported scripts: {sorted(unexpected)}")

    files: dict[str, str] = {}
    nodes: list[NodeSpec] = []
    serves: list[VllmServeCommand] = []
    role_nodes: dict[str, list[tuple[int, ExternalDpCommand]]] = {
        "prefill": [],
        "decode": [],
    }
    global_index = 0
    for role in ("prefill", "decode"):
        indexes = sorted(grouped[role])
        if indexes != list(range(counts[role])):
            raise _fail(
                f"case requires {counts[role]} contiguous {role} nodes; "
                f"found indexes {indexes}"
            )
        role_launches: list[ExternalDpCommand] = []
        for role_index in indexes:
            pair = grouped[role][role_index]
            if set(pair) != {"template", "launch"}:
                raise _fail(
                    f"{role}-{role_index} requires one template and one launch script"
                )
            template_name = pair["template"]
            launch_name = pair["launch"]
            template_text = _script(source, template_name)
            launch_text = _script(source, launch_name)
            serve = _analyze(template_name, parse_vllm_serve, template_text)
            launch = _analyze(launch_name, parse_external_dp, launch_text)
            _validate_pd_template(template_name, role, serve, launch)
            _require_variable(
                launch.dp_address,
                f"{role.upper()}_NODE_{role_index}_IP",
                f"script {launch_name!r} --dp-address",
            )
            if launch.dp_size_local * launch.tp_size != source.npu_per_node:
                raise _fail(
                    f"script {launch_name!r}: dp-size-local * tp-size must equal "
                    f"npu_per_node ({source.npu_per_node})"
                )
            expected_rank = role_index * launch.dp_size_local
            if launch.dp_rank_start != expected_rank:
                raise _fail(
                    f"script {launch_name!r}: dp-rank-start must be {expected_rank}"
                )
            _port(launch.dp_rpc_port, f"script {launch_name!r} --dp-rpc-port")
            _port(launch.vllm_start_port, f"script {launch_name!r} start port")
            _port(
                launch.vllm_start_port + launch.dp_size_local - 1,
                f"script {launch_name!r} final service port",
            )
            role_launches.append(launch)
            serves.append(serve)
            node_dir = f"nodes/node{global_index}"
            files[f"{node_dir}/run.sh"] = _pd_launcher(launch)
            files[f"{node_dir}/run_dp_template.sh"] = _pd_template(
                serve, template_text
            )
            nodes.append(
                NodeSpec(
                    id=f"node{global_index}",
                    role=role,
                    launch=f"{node_dir}/run.sh",
                    readiness=ReadinessSpec(
                        port_start=launch.vllm_start_port,
                        count=launch.dp_size_local,
                    ),
                )
            )
            role_nodes[role].append((global_index, launch))
            global_index += 1
        dp_sizes = {item.dp_size for item in role_launches}
        local_sizes = {item.dp_size_local for item in role_launches}
        tp_sizes = {item.tp_size for item in role_launches}
        if len(dp_sizes) != 1 or len(local_sizes) != 1 or len(tp_sizes) != 1:
            raise _fail(f"all {role} launchers must use the same DP/TP topology")
        expected_dp = counts[role] * role_launches[0].dp_size_local
        if role_launches[0].dp_size != expected_dp:
            raise _fail(f"{role} global dp-size must be {expected_dp}")

    gateway_text = _script(source, "gateway-0")
    gateway = _analyze("gateway-0", parse_gateway, gateway_text)
    _require_variable(gateway.host, "GATEWAY_NODE_0_IP", "gateway --host")
    _port(gateway.port, "gateway --port")
    for _, launch in role_nodes["prefill"] + role_nodes["decode"]:
        if launch.vllm_start_port <= gateway.port < (
            launch.vllm_start_port + launch.dp_size_local
        ):
            raise _fail("gateway port must not overlap a backend service port")
    expected_prefill = sum(x.dp_size_local for _, x in role_nodes["prefill"])
    expected_decode = sum(x.dp_size_local for _, x in role_nodes["decode"])
    if len(gateway.prefiller_hosts) != expected_prefill or len(
        gateway.prefiller_ports
    ) != expected_prefill:
        raise _fail("gateway Prefill endpoints do not match external-DP services")
    if len(gateway.decoder_hosts) != expected_decode or len(
        gateway.decoder_ports
    ) != expected_decode:
        raise _fail("gateway Decode endpoints do not match external-DP services")
    expected_prefill_hosts: list[str] = []
    expected_prefill_ports: list[int] = []
    expected_decode_hosts: list[str] = []
    expected_decode_ports: list[int] = []
    for role, host_values, port_values in (
        ("prefill", expected_prefill_hosts, expected_prefill_ports),
        ("decode", expected_decode_hosts, expected_decode_ports),
    ):
        for role_index, (_, launch) in enumerate(role_nodes[role]):
            host_values.extend(
                f"{role.upper()}_NODE_{role_index}_IP"
                for _ in range(launch.dp_size_local)
            )
            port_values.extend(
                launch.vllm_start_port + offset
                for offset in range(launch.dp_size_local)
            )
    actual_prefill_hosts = []
    for index, value in enumerate(gateway.prefiller_hosts):
        match = _SHELL_VARIABLE.fullmatch(value)
        if match is None:
            raise _fail(f"gateway prefiller host {index} must be a shell variable")
        actual_prefill_hosts.append(match.group("name"))
    actual_decode_hosts = []
    for index, value in enumerate(gateway.decoder_hosts):
        match = _SHELL_VARIABLE.fullmatch(value)
        if match is None:
            raise _fail(f"gateway decoder host {index} must be a shell variable")
        actual_decode_hosts.append(match.group("name"))
    if actual_prefill_hosts != expected_prefill_hosts:
        raise _fail("gateway Prefill hosts do not match the declared Prefill nodes")
    if list(gateway.prefiller_ports) != expected_prefill_ports:
        raise _fail("gateway Prefill ports do not match launcher service ports")
    if actual_decode_hosts != expected_decode_hosts:
        raise _fail("gateway Decode hosts do not match the declared Decode nodes")
    if list(gateway.decoder_ports) != expected_decode_ports:
        raise _fail("gateway Decode ports do not match launcher service ports")
    files["gateway/run.sh"] = _gateway_script(gateway, role_nodes)
    return (
        tuple(nodes),
        GatewaySpec(launch="gateway/run.sh", port=gateway.port),
        files,
        serves,
    )


def _plan_non_pd(
    source: ScenarioSource, node_count: int
) -> tuple[tuple[NodeSpec, ...], dict[str, str], list[VllmServeCommand]]:
    """Expand one API node and contiguous headless internal-DP nodes."""
    expected = {"api-0", "service-check"}
    headless: dict[int, str] = {}
    for name in source.scripts:
        match = _HEADLESS_NAME.fullmatch(name)
        if match:
            index = int(match.group("index"))
            headless[index] = name
            expected.add(name)
    unexpected = set(source.scripts) - expected
    if unexpected:
        raise _fail(f"non-PD scenario has unsupported scripts: {sorted(unexpected)}")
    indexes = sorted(headless)
    if indexes != list(range(node_count - 1)):
        raise _fail(
            f"case requires {node_count - 1} contiguous headless nodes; "
            f"found indexes {indexes}"
        )

    api_text = _script(source, "api-0")
    api = _analyze("api-0", parse_vllm_serve, api_text)
    if "--headless" in api.options:
        raise _fail("script 'api-0' must expose the API, not use --headless")
    served_name = _option(api, "--served-model-name", "api-0")
    port = _literal_integer(_option(api, "--port", "api-0"), "api-0 --port")
    _port(port, "api-0 --port")
    global_dp = _literal_integer(
        _option(api, "--data-parallel-size", "api-0"),
        "api-0 --data-parallel-size",
    )
    local_dp = _literal_integer(
        _option(api, "--data-parallel-size-local", "api-0"),
        "api-0 --data-parallel-size-local",
    )
    tp = _literal_integer(
        _option(api, "--tensor-parallel-size", "api-0"),
        "api-0 --tensor-parallel-size",
    )
    if global_dp != node_count * local_dp:
        raise _fail("internal DP size must equal node count * local DP size")
    if local_dp * tp != source.npu_per_node:
        raise _fail("internal local DP size * TP size must equal npu_per_node")
    api_address = _option(api, "--data-parallel-address", "api-0")
    _require_variable(api_address, "API_NODE_0_IP", "api-0 --data-parallel-address")
    _require_variable(
        _option(api, "--host", "api-0"), "API_NODE_0_IP", "api-0 --host"
    )
    api_rpc_port = _option(api, "--data-parallel-rpc-port", "api-0")
    _port(
        _literal_integer(api_rpc_port, "api-0 --data-parallel-rpc-port"),
        "api-0 --data-parallel-rpc-port",
    )
    if int(api_rpc_port) == port:
        raise _fail("api service port and data-parallel RPC port must differ")

    files = {"nodes/node0/run.sh": _internal_launcher(api, api_text, api=True)}
    nodes = [
        NodeSpec(
            id="node0",
            role="api",
            launch="nodes/node0/run.sh",
            readiness=ReadinessSpec(port_start=port),
        )
    ]
    serves = [api]
    for headless_index in indexes:
        name = headless[headless_index]
        text = _script(source, name)
        serve = _analyze(name, parse_vllm_serve, text)
        serves.append(serve)
        if "--headless" not in serve.options:
            raise _fail(f"script {name!r} requires --headless")
        checks = {
            "--data-parallel-size": str(global_dp),
            "--data-parallel-size-local": str(local_dp),
            "--data-parallel-start-rank": str((headless_index + 1) * local_dp),
            "--data-parallel-address": api_address,
            "--data-parallel-rpc-port": api_rpc_port,
            "--tensor-parallel-size": str(tp),
        }
        for option, wanted in checks.items():
            actual = _option(serve, option, name)
            if actual != wanted:
                raise _fail(
                    f"script {name!r}: {option} must be {wanted!r}, got {actual!r}"
                )
        global_index = headless_index + 1
        path = f"nodes/node{global_index}/run.sh"
        files[path] = _internal_launcher(serve, text, api=False)
        nodes.append(
            NodeSpec(
                id=f"node{global_index}",
                role="headless",
                launch=path,
                readiness=None,
            )
        )
    return tuple(nodes), files, serves


def _evaluation_stages(
    selections: Sequence[str], defaults: Mapping[str, object]
) -> tuple[tuple[StageSpec, ...], dict[str, str]]:
    """Build completion plus selected shared AISBench stages and adapters."""
    stages: list[StageSpec] = [
        StageSpec(
            id="completion",
            failure_category="check_failed",
            steps=(
                StepSpec(
                    id="completion",
                    script="checks/completion.sh",
                    timeout_seconds=300,
                    inputs={},
                ),
            ),
        )
    ]
    files = {"checks/completion.sh": _COMPLETION_SCRIPT}
    if selections:
        files["evaluations/run_aisbench.sh"] = _AISBENCH_SCRIPT
    for kind in selections:
        raw = defaults.get(kind)
        if not isinstance(raw, Mapping):
            raise _fail(f"AISBench defaults are missing mapping {kind!r}")
        unknown = set(raw) - {"step_id", "timeout_seconds", "inputs"}
        if unknown:
            raise _fail(f"AISBench defaults {kind!r} has unknown keys: {sorted(unknown)}")
        step_id = raw.get("step_id")
        timeout = raw.get("timeout_seconds")
        inputs = raw.get("inputs")
        if not isinstance(step_id, str) or not step_id:
            raise _fail(f"AISBench defaults {kind!r}.step_id must be text")
        if type(timeout) is not int or timeout <= 0:
            raise _fail(
                f"AISBench defaults {kind!r}.timeout_seconds must be positive"
            )
        if not isinstance(inputs, Mapping):
            raise _fail(f"AISBench defaults {kind!r}.inputs must be a mapping")
        step_inputs: Mapping[str, object]
        if set(inputs) == {"aisbench"} and isinstance(inputs["aisbench"], Mapping):
            step_inputs = inputs
        else:
            step_inputs = {"aisbench": dict(inputs)}
        stages.append(
            StageSpec(
                id=kind,
                failure_category="evaluation_failed",
                steps=(
                    StepSpec(
                        id=step_id,
                        script="evaluations/run_aisbench.sh",
                        timeout_seconds=timeout,
                        inputs=step_inputs,
                    ),
                ),
            )
        )
    return tuple(stages), files


def plan_scenario(
    source: ScenarioSource,
    plan_name: str,
    parameter_digest: str,
    aisbench_defaults: Mapping[str, object],
) -> BundleSpec:
    """Validate and plan one rendered PD or internal-DP Recipe scenario."""
    if not plan_name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", plan_name):
        raise _fail("plan name must be a lowercase kebab-case identifier")
    _positive(source.npu_per_node, "npu_per_node")
    if not source.npu.strip():
        raise _fail("npu must not be empty")
    if len(set(source.aisbench)) != len(source.aisbench):
        raise _fail("aisbench selections must be unique")
    unsupported = set(source.aisbench) - {"accuracy", "performance"}
    if unsupported:
        raise _fail(f"unsupported AISBench selections: {sorted(unsupported)}")
    if "service-check" not in source.scripts:
        raise _fail("scenario requires service-check")

    gateway: GatewaySpec | None
    if _deployment_kind(source.deployment) == "pd":
        case = _parse_pd_case(source.deployment, source.case)
        if case is None:
            raise _fail(
                "PD case must use the exact <P>p<D>d form "
                "(or a legacy form like '1P1D (1 Prefill node + 1 Decode node)')"
            )
        counts = {key: int(case.group(key)) for key in ("prefill", "decode")}
        nodes, gateway, files, serves = _plan_pd(source, counts)
        endpoint_port = gateway.port
    elif _deployment_kind(source.deployment) == "non-pd":
        case = _NON_PD_CASE.fullmatch(source.case)
        if case is None:
            raise _fail("non-PD case must use the exact <N>-node form")
        node_count = int(case.group("nodes"))
        if node_count < 2:
            raise _fail("internal multi-node DP requires at least two nodes")
        nodes, files, serves = _plan_non_pd(source, node_count)
        gateway = None
        assert nodes[0].readiness is not None
        endpoint_port = nodes[0].readiness.port_start
    else:
        raise _fail("deployment must be exactly 'pd' or 'non-pd'")

    model_id = _consistent([serve.model_id for serve in serves], "model id")
    served_values = [
        value
        for serve in serves
        if (value := serve.options.get("--served-model-name")) is not None
    ]
    if not served_values:
        raise _fail("an API service/template must declare --served-model-name")
    served_name = _consistent(served_values, "served model name")
    check_text = _script(source, "service-check")
    check = _analyze("service-check", parse_completion_check, check_text)
    if check.served_name != served_name:
        raise _fail(
            "service-check model must match the service --served-model-name "
            f"({served_name!r})"
        )
    if f":{endpoint_port}/v1/completions" not in check.endpoint:
        raise _fail(
            f"service-check endpoint must use planned service port {endpoint_port}"
        )

    stages, stage_files = _evaluation_stages(source.aisbench, aisbench_defaults)
    files.update(stage_files)
    try:
        recipe_path = source.recipe_path.resolve()
        source_recipe = recipe_path.relative_to(_REPOSITORY_ROOT).as_posix()
    except ValueError as exc:
        raise _fail(
            f"source Recipe must be inside repository {_REPOSITORY_ROOT}: "
            f"{source.recipe_path}"
        ) from exc
    try:
        recipe_digest = hashlib.sha256(recipe_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise _fail(f"cannot hash source Recipe {source.recipe_path}: {exc}") from exc
    return BundleSpec(
        name=plan_name,
        source_recipe=source_recipe,
        test_id=source.test_id,
        recipe_digest=recipe_digest,
        parameter_digest=parameter_digest,
        model_id=model_id,
        served_name=served_name,
        npu_per_node=source.npu_per_node,
        nodes=nodes,
        gateway=gateway,
        stages=stages,
        files=files,
    )
