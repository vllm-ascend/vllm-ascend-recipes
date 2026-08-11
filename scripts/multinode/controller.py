#!/usr/bin/env python3
"""controller.py — CPU-side controller for the multi-node PD recipe verify pipeline.

Runs on the self-hosted runner (``linux-aarch64-a2b4-0``), which has kubectl +
kubeconfig access to the cluster. The runner itself has no NPU; the model runs
in LWS pods scheduled across the 910B4 nodes.

Responsibilities:
  extract  -> parse the recipe, pick the multi-node PD scenario, build plan.json
  apply    -> render LWS + ConfigMap (node_entry.py + plan.json) and kubectl apply
  wait     -> wait until all pods are Ready, then poll the prefill endpoint
  verify   -> run the recipe's Service Verification curls
  eval     -> optional lightweight latency check
  cleanup  -> always delete the LWS / ConfigMap
  summary  -> write a stages-map summary.json to --results-dir

No PVC / NFS is required: peer IPs are resolved inside the pods via the LWS
headless services (hostNetwork pods), weights ride a hostPath volume, and
plan.json rides a ConfigMap.

``--dry-run`` renders plan + LWS + ConfigMap to --results-dir and exits without
touching kubectl.
"""

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("controller.py requires PyYAML: python3 -m pip install pyyaml",
          file=sys.stderr)
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

DEFAULT_IMAGE = "swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:v0.23.0rc1"
# The controller's kubeconfig authenticates as the runner's own in-cluster
# ServiceAccount, which has permissions only inside its own namespace (no
# cluster-scope node/namespace access). The multi-node LWS pods therefore run
# in that same namespace — separation from the single-node pipeline is still
# intact (a2b4-8 runs vllm directly, no pods). No cluster-scope operations
# (get nodes / create namespace) are used anywhere in the controller.
DEFAULT_NAMESPACE = "vllm-ascend-vllm-ascend-recipes"
DEFAULT_CHIP = "910B4"
NPU_RESOURCE = "huawei.com/ascend-1980"  # the cluster's actual Ascend NPU resource name


class PipelineError(RuntimeError):
    def __init__(self, stage: str, msg: str):
        super().__init__(msg)
        self.stage = stage


class Stages:
    def __init__(self):
        self.map = {}
        self.failed = None

    def ok(self, name: str) -> None:
        self.map[name] = "pass"
        print(f"[controller] stage {name}: pass", flush=True)

    def fail(self, name: str, msg: str) -> None:
        self.map[name] = "fail"
        self.failed = name
        print(f"[controller] stage {name}: FAIL — {msg}", flush=True)


# ---------------------------------------------------------------------------
# Recipe extraction
# ---------------------------------------------------------------------------

def shell_blocks(content: str) -> list[str]:
    return re.findall(r"```(?:bash|shell)\s*\n(.*?)```", content, re.DOTALL)


def python_blocks(content: str) -> list[str]:
    return re.findall(r"```python\s*\n(.*?)```", content, re.DOTALL)


def split_launch_commands(block: str) -> list[str]:
    """Split a shell block into individual ``python launch_online_dp.py ...``
    invocations. Recipes may put prefill + decode commands in ONE block
    (Qwen3.6: ``# p0 (Prefill node 0)`` / ``# d0 (Decode node 0)``). Drop
    trailing comment-only lines left over from the split."""
    parts = re.split(r"(?=^\s*python\s+launch_online_dp\.py\b)", block, flags=re.M)
    cmds = []
    for p in parts:
        if "launch_online_dp.py" not in p:
            continue
        lines = [ln for ln in p.splitlines() if ln.strip()]
        while lines and lines[-1].lstrip().startswith("#"):
            lines.pop()
        cmds.append("\n".join(lines).strip())
    return cmds


def fill_dotted_ips(text: str, ips: list[str]) -> str:
    """Replace ``141.xx.xx.N`` / ``xx.xx.xx.N`` placeholders with node IPs
    (the recipe's last octet is a 1-based node/role index)."""
    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        return ips[idx - 1] if 0 < idx <= len(ips) else m.group(0)
    return re.sub(r"\b(?:\d+\.)?(?:x+\.){2,3}(\d+)\b", repl, text)


def resolve_model_cache(data: dict) -> str:
    """Map ``model.model_id`` → on-node weights path via models/_cache_paths.yaml
    (mirrors verify-recipe.sh). Pods mount hostPath at /root/.cache/modelscope,
    so the single-node CACHE_BASE layout applies inside the pod too."""
    model_id = (data.get("model", {}) or {}).get("model_id", "")
    cache_file = REPO_ROOT / "models" / "_cache_paths.yaml"
    if not model_id or not cache_file.exists():
        return ""
    try:
        aliases = (yaml.safe_load(cache_file.read_text(encoding="utf-8")) or {}) \
            .get("aliases") or []
    except Exception:
        return ""
    for a in aliases:
        if a.get("model_id") == model_id and a.get("cache_dir"):
            return f"/root/.cache/modelscope/hub/models/{a['cache_dir']}"
    return ""


def parse_recipe(args) -> dict:
    with open(args.recipe, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    target = None
    want_deployment = args.deployment_filter.strip().lower()
    for s in data.get("scenarios", []):
        # Prefer an exact `strategy` match when the caller routes by strategy
        # (pd_cluster / multi_node_dp / single_node_A2 / ...). This lets the
        # workflow pick the right scenario even when its NPU differs from the
        # default hw-filter (e.g. GLM-5 PD is A3 while hw_filter defaults A2).
        if args.strategy and s.get("strategy") == args.strategy:
            target = s
            break
        npu = s.get("npu", "")
        deployment = s.get("deployment", "")
        # Case-insensitive substring so the same filter works across the
        # recipe's language (DeepSeek uses 多节点-PD分离, Qwen uses
        # Multi-Node PD Separation — both contain "PD").
        if args.hw_filter in npu and want_deployment in deployment.strip().lower():
            target = s
            break
    if target is None:
        raise PipelineError(
            "extract",
            f"no scenario matching hw={args.hw_filter!r} deployment={args.deployment_filter!r} "
            f"in {args.recipe}")

    plan = {
        "run_id": args.run_id,
        "lws_name": f"recipe-{args.run_id}",
        "namespace": args.namespace,
        "recipe": args.recipe,
        "scenario": {
            "npu": target.get("npu", ""),
            "precision": target.get("precision", ""),
            "deployment": target.get("deployment", ""),
            "case": target.get("case", ""),
        },
        "topology": {"prefill": args.prefill_nodes, "decode": args.decode_nodes},
        "npu_per_node": args.npu_per_node,
        "role_templates": {},
        "node_templates": [],
        "mode": target.get("strategy", ""),
        "launch_cmds": {},
        "launch_online_dp_py": "",
        "verify_cmds": [],
        "needs_proxy": False,
        "model_cache_path": resolve_model_cache(data),
    }

    launch_blocks: list[str] = []
    for step in target.get("steps", []):
        title = step.get("title", "")
        content = step.get("content", "")
        shell = shell_blocks(content)
        py = python_blocks(content)
        tl = title.lower()
        # ASCII + zh keywords (recipes mix both: Qwen en uses English titles,
        # zh mirror uses 启动/验证). Proxy is checked BEFORE launch because the
        # zh proxy title "在 Prefill 主节点上启动 proxy" contains 启动.
        if "launch_online_dp.py" in title and py and not plan["launch_online_dp_py"]:
            plan["launch_online_dp_py"] = py[0]
        elif "prefill" in tl and shell and "prefill" not in plan["role_templates"]:
            plan["role_templates"]["prefill"] = shell[0]
        elif "decode" in tl and shell and "decode" not in plan["role_templates"]:
            plan["role_templates"]["decode"] = shell[0]
        elif "proxy" in tl:
            plan["needs_proxy"] = True
            if shell and not plan.get("proxy_command"):
                plan["proxy_command"] = shell[0]
        elif ("launch" in tl or "启动" in tl
              or "start the service" in tl or "start the server" in tl):
            for block in shell:
                launch_blocks.extend(split_launch_commands(block))
        elif "verify" in tl or "验证" in tl or tl.endswith("verification"):
            plan["verify_cmds"].extend(shell)

    # Multi-node DP (no prefill/decode split): every shell block in the
    # scenario is one node's template (recipe order: node 0, node 1, ...).
    if plan["mode"] == "multi_node_dp":
        node_blocks = [b for step in target.get("steps", [])
                       for b in shell_blocks(step.get("content", ""))]
        if not node_blocks:
            raise PipelineError("extract", "multi_node_dp scenario has no shell blocks")
        plan["node_templates"] = node_blocks
        plan["topology"]["prefill"] = args.prefill_nodes or len(node_blocks)
        plan["topology"]["decode"] = args.decode_nodes or 0
    else:
        # First launch block -> prefill, second -> decode (recipe order).
        if len(launch_blocks) >= 2:
            plan["launch_cmds"]["prefill"] = launch_blocks[0]
            plan["launch_cmds"]["decode"] = launch_blocks[1]

    # Recipes embed launch_online_dp.py (DeepSeek) or only reference it (Qwen);
    # fall back to the vendored upstream copy so the pod always has it.
    if not plan["launch_online_dp_py"]:
        vendor = SCRIPT_DIR / "launch_online_dp.py"
        if not vendor.exists():
            raise PipelineError("extract",
                                "recipe has no embedded launch_online_dp.py and the "
                                "vendored scripts/multinode/launch_online_dp.py is missing")
        plan["launch_online_dp_py"] = vendor.read_text(encoding="utf-8")

    if plan["mode"] == "multi_node_dp":
        # DP pods run their own per-node template; no prefill/decode roles.
        if plan["npu_per_node"] == 0:
            plan["npu_per_node"] = _parse_dp_npu_per_node(plan["node_templates"][0])
    else:
        missing_tpl = [k for k in ("prefill", "decode") if k not in plan["role_templates"]]
        missing_launch = [k for k in ("prefill", "decode") if k not in plan["launch_cmds"]]
        if missing_tpl:
            raise PipelineError("extract", f"role template missing for: {missing_tpl}")
        if missing_launch:
            raise PipelineError("extract", f"launch command missing for: {missing_launch}")

    # Topology: an explicit input wins; 0 = auto-derive from the launch command
    # (nodes = dp-size // dp-size-local, npu-per-node = dp-size-local × tp-size).
    # multi_node_dp derives its topology from node_templates instead.
    if plan["mode"] == "multi_node_dp":
        pass
    elif plan["topology"]["prefill"] == 0 or plan["topology"]["decode"] == 0 \
            or plan["npu_per_node"] == 0:
        auto_topology, auto_npu = _parse_launch_topology(plan)
        if plan["topology"]["prefill"] == 0:
            plan["topology"]["prefill"] = auto_topology["prefill"]
        if plan["topology"]["decode"] == 0:
            plan["topology"]["decode"] = auto_topology["decode"]
        if plan["npu_per_node"] == 0:
            plan["npu_per_node"] = auto_npu

    return plan


def _parse_launch_topology(plan: dict) -> tuple[dict, int]:
    """Derive (prefill_nodes, decode_nodes, npu_per_node) from the launch
    commands: nodes = dp-size // dp-size-local, npu-per-node = dp-size-local ×
    tp-size. Raises if the commands can't be parsed."""
    topology: dict[str, int] = {}
    npu = 0
    for role in ("prefill", "decode"):
        raw = plan["launch_cmds"].get(role, "")
        dp = re.search(r"--dp-size\s+(\d+)", raw)
        dpl = re.search(r"--dp-size-local\s+(\d+)", raw)
        tp = re.search(r"--tp-size\s+(\d+)", raw)
        if not (dp and dpl and tp):
            raise PipelineError(
                "extract",
                f"cannot auto-derive {role} topology from launch command "
                f"(missing --dp-size/--dp-size-local/--tp-size): {raw[:120]}")
        dp_size, dp_local, tp_size = int(dp.group(1)), int(dpl.group(1)), int(tp.group(1))
        if dp_local <= 0 or dp_size % dp_local != 0:
            raise PipelineError(
                "extract",
                f"{role}: dp-size {dp_size} not divisible by dp-size-local {dp_local}")
        topology[role] = dp_size // dp_local
        npu = max(npu, dp_local * tp_size)
    return topology, npu


def _parse_dp_npu_per_node(template: str) -> int:
    """Derive npu-per-node for a multi-node DP template from its vllm serve
    flags: npu = dp-size-local × tensor-parallel-size."""
    dpl = re.search(r"--data-parallel-size-local\s+(\d+)", template)
    tp = re.search(r"--tensor-parallel-size\s+(\d+)", template)
    if not (dpl and tp):
        raise PipelineError(
            "extract",
            "cannot derive npu-per-node from multi_node_dp template "
            "(missing --data-parallel-size-local / --tensor-parallel-size)")
    return int(dpl.group(1)) * int(tp.group(1))


# ---------------------------------------------------------------------------
# LWS / ConfigMap rendering
# ---------------------------------------------------------------------------

def _volumes(npu_per_node: int, entry_cm: str, pvc_name: str = "") -> list[dict]:
    volumes = []
    # Model weights: a node-local hostPath only works if every schedulable node
    # has them. PR #34 mounts the shared RWX cache PVC at /root/.cache — do the
    # same (pvc_name defaults to the cluster's shared cache volume).
    if pvc_name:
        volumes.append({"name": "model-cache",
                        "persistentVolumeClaim": {"claimName": pvc_name}})
    else:
        volumes.append({"name": "model-cache",
                        "hostPath": {"path": "/root/.cache/modelscope"}})
    # Driver + tools (hccn_tool, lib64, version.info...) straight from the
    # host — same as the proven PR #34 LWS. Do NOT mount /dev/davinciN from
    # the host: the Ascend device plugin injects the NPU device nodes and
    # ASCEND_RT_VISIBLE_DEVICES itself; hostPath device mounts conflict with
    # it and leave the env unset (vllm then targets the wrong devices).
    volumes.append({"name": "driver-tools",
                    "hostPath": {"path": "/usr/local/Ascend/driver"}})
    volumes.append({"name": "worklogs", "emptyDir": {}})
    volumes.append({"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "16Gi"}})
    volumes.append({"name": "workdir", "emptyDir": {}})
    volumes.append({"name": "entry", "configMap": {"name": entry_cm}})
    return volumes


def _volume_mounts(npu_per_node: int) -> list[dict]:
    return [
        {"name": "driver-tools", "mountPath": "/usr/local/Ascend/driver"},
        {"name": "model-cache", "mountPath": "/root/.cache"},
        {"name": "worklogs", "mountPath": "/root/ascend/log"},
        {"name": "shm", "mountPath": "/dev/shm"},
        {"name": "workdir", "mountPath": "/run/recipe-ci"},
        {"name": "entry", "mountPath": "/scripts"},
    ]


def _pod_spec(plan: dict, args, entry_cm: str, role: str = "") -> dict:
    npu = plan.get("npu_per_node") or args.npu_per_node
    lws = plan["lws_name"]
    labels = {"multinode-lws": lws}
    if role:
        labels["role"] = role
    return {
        "metadata": {"labels": labels},
        "spec": {
            "hostNetwork": True,
            # hostNetwork pods default to dnsPolicy: Default (the node's
            # resolv.conf), which has no cluster search domains -> the LWS
            # headless service DNS (<pod>.<group>.<ns>.svc.cluster.local)
            # would NOT resolve and node_entry could not find its peers.
            # ClusterFirstWithHostNet routes hostNetwork-pod DNS through
            # CoreDNS with the pod's search domains, fixing peer resolution.
            "dnsPolicy": "ClusterFirstWithHostNet",
            # No pod-level restartPolicy: the CCE LWS addon (cceaddon-lws-
            # controller-manager) creates a StatefulSet per subgroup, and K8s
            # rejects StatefulSet pod templates with restartPolicy != "Always".
            # Omitting it leaves the default "Always"; failed-pod diagnosis
            # still works (CrashLoopBackOff pods are loggable).
            # NPU nodes carry dedicated=night:NoSchedule; without this
            # toleration LWS pods stay Pending forever (the proven runner LWS
            # from PR #34 uses the same toleration).
            "tolerations": [{
                "key": "dedicated",
                "operator": "Equal",
                "value": "night",
                "effect": "NoSchedule",
            }],
            # SOFT affinity for the NPU chip: the pod already requests
            # NPU_RESOURCE (huawei.com/ascend-1980), which is what actually
            # pins it to NPU-capable nodes. A *required* nodeAffinity on the
            # chip label used to block scheduling entirely when nodes didn't
            # carry node.kubernetes.io/npu.chip.name={args.chip}; as a
            # preference it still favors the target chip but lets the pod land
            # on any NPU node.
            "affinity": {
                "nodeAffinity": {
                    "preferredDuringSchedulingIgnoredDuringExecution": [{
                        "weight": 100,
                        "preference": {
                            "matchExpressions": [{
                                "key": "node.kubernetes.io/npu.chip.name",
                                "operator": "In",
                                "values": [args.chip],
                            }]
                        },
                    }]
                },
                # PD separation REQUIRES prefill and decode on different nodes
                # (they use the same DP RPC port 12321 — co-locating them gives
                # "Address already in use"). PR #34 pins 1 pod/node via
                # podAntiAffinity; do the same on a shared LWS label +
                # kubernetes.io/hostname.
                "podAntiAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [{
                        "labelSelector": {
                            "matchLabels": {"multinode-lws": lws},
                        },
                        "topologyKey": "kubernetes.io/hostname",
                    }]
                },
            },
            "containers": [{
                "name": "vllm-ascend",
                "image": args.image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["python3", "/scripts/node_entry.py"],
                "env": [
                    {"name": "MULTINODE_WORKDIR", "value": "/run/recipe-ci"},
                    {"name": "MULTINODE_PLAN", "value": "/scripts/plan.json"},
                    {"name": "RUN_ID", "value": args.run_id},
                    # Same env as the proven PR #34 runner LWS: spawn (fork
                    # after torch/driver threads have started corrupts the
                    # heap — "corrupted size vs. prev_size" in engine init),
                    # ModelScope with HF offline (the pre-downloaded local dir
                    # is returned as-is by snapshot_download), and reduced
                    # logging for the multi-engine pods.
                    {"name": "VLLM_WORKER_MULTIPROC_METHOD", "value": "spawn"},
                    {"name": "VLLM_USE_MODELSCOPE", "value": "True"},
                    {"name": "HF_HUB_OFFLINE", "value": "1"},
                    {"name": "VLLM_LOGGING_LEVEL", "value": "ERROR"},
                    {"name": "TORCH_DEVICE_BACKEND_AUTOLOAD", "value": "0"},
                    # Mooncake runtime .so lives under site-packages/mooncake;
                    # the mooncake-enabled image bakes this via ENV too, this
                    # is insurance.
                    {"name": "LD_LIBRARY_PATH",
                     "value": "/usr/local/lib64/python3.12/site-packages/mooncake"},
                ],
                "securityContext": {"privileged": True},
                "resources": {
                    "limits": {NPU_RESOURCE: npu,
                               "memory": f"{npu * 64}Gi",
                               "ephemeral-storage": "20Gi"},
                    # CPU request scales with NPU count (4/NPU) — a hardcoded
                    # 125 (and later 8/NPU) made pods Unschedulable on 910B4
                    # nodes that were short on free CPU; 4/NPU fits 4-card
                    # nodes (tp*dp=4). Memory also scales (64Gi/NPU): a flat
                    # 512Gi request was Unschedulable on nodes that don't have
                    # 512Gi free. Ephemeral storage is capped at 20Gi to match
                    # the working PR #34 LWS (100Gi was Unschedulable on some
                    # nodes).
                    "requests": {NPU_RESOURCE: npu,
                                 "cpu": str(npu * 4),
                                 "memory": f"{npu * 64}Gi",
                                 "ephemeral-storage": "20Gi"},
                },
                "volumeMounts": _volume_mounts(npu),
            }],
            "volumes": _volumes(npu, entry_cm, getattr(args, "pvc_name", "")),
        },
    }


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else line
                     for line in text.splitlines())


def render_lws(plan: dict, args) -> str:
    """Render the LeaderWorkerSet from scripts/multinode/lws.yaml.jinja2 —
    aligned with upstream _e2e_nightly_multi_node.yaml. Simple string
    substitution (no jinja2 dependency on the controller runner)."""
    size = plan["topology"]["prefill"] + plan["topology"]["decode"]
    entry_cm = f"recipe-entry-{args.run_id}"
    # _pod_spec returns a full PodTemplateSpec ({metadata, spec}); inject it at
    # the leaderTemplate / workerTemplate level (indent 6 = child of a key at
    # 4 spaces). Injecting it under `spec:` instead would nest metadata inside
    # PodSpec, which the API server rejects with "unknown field metadata".
    leader_spec = _indent(
        yaml.safe_dump(_pod_spec(plan, args, entry_cm, role="leader"),
                       sort_keys=False).rstrip(), 6)
    worker_spec = _indent(
        yaml.safe_dump(_pod_spec(plan, args, entry_cm, role="worker"),
                       sort_keys=False).rstrip(), 6)
    tpl = (SCRIPT_DIR / "lws.yaml.jinja2").read_text(encoding="utf-8")
    values = {
        "LWS_NAME": plan["lws_name"],
        "NAMESPACE": args.namespace,
        "SIZE": str(size),
        "LEADER_SPEC": leader_spec,
        "WORKER_SPEC": worker_spec,
    }
    for key, value in values.items():
        tpl = tpl.replace("{{ " + key + " }}", value)
    return tpl


def render_configmap(plan: dict, node_entry_src: str, args) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"recipe-entry-{args.run_id}", "namespace": args.namespace},
        "data": {
            "node_entry.py": node_entry_src,
            "plan.json": json.dumps(plan, indent=2),
        },
    }


# ---------------------------------------------------------------------------
# kubectl wrappers
# ---------------------------------------------------------------------------

def kubectl_binary() -> str:
    """Locate kubectl even if it's not on the Actions runner's PATH. Falls back
    to bare ``kubectl`` so the shell produces a clear error if truly absent."""
    import shutil
    exe = shutil.which("kubectl")
    if exe:
        return exe
    for p in ("/usr/bin/kubectl", "/usr/local/bin/kubectl",
              "/opt/kubectl/kubectl", "/snap/bin/kubectl"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "kubectl"


def ensure_kubeconfig() -> str:
    """a2b4-0 is the K8s scheduling node, so kubectl + a kubeconfig exist — but
    the Actions runner process may not inherit them (PATH / KUBECONFIG / HOME).
    Look in the common locations and export one so the controller can drive the
    cluster."""
    if os.environ.get("KUBECONFIG") and os.path.isfile(os.environ["KUBECONFIG"]):
        return os.environ["KUBECONFIG"]
    for p in (os.path.expanduser("~/.kube/config"),
              "/root/.kube/config",
              "/etc/kubernetes/admin.conf",
              os.path.expanduser("$HOME/.kube/config")):
        if os.path.isfile(p):
            os.environ["KUBECONFIG"] = p
            return p
    return os.environ.get("KUBECONFIG", "")


def kubectl(args_str: str, args, check: bool = True, stage: str = "run") -> int:
    ensure_kubeconfig()
    cmd = f"{kubectl_binary()} {args_str} -n {args.namespace}"
    print(f"[controller] $ {cmd}", flush=True)
    rc = subprocess.run(cmd, shell=True).returncode
    if check and rc != 0:
        raise PipelineError(stage, f"kubectl {args_str} failed rc={rc}")
    return rc


def kubectl_capture(args_str: str, args) -> str | None:
    ensure_kubeconfig()
    cmd = f"{kubectl_binary()} {args_str} -n {args.namespace}"
    rc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if rc.returncode != 0:
        print(f"[controller] kubectl {args_str} failed: {rc.stderr}", file=sys.stderr)
        return None
    return rc.stdout


def pod_ips(selector: str, args) -> list[str]:
    out = kubectl_capture(f"get pods -l {selector} -o json", args)
    if not out:
        return []
    data = json.loads(out)

    def index_key(p: dict) -> int:
        try:
            return int(p.get("metadata", {}).get("labels", {})
                       .get("leaderworkerset.sigs.k8s.io/worker-index", "0"))
        except (TypeError, ValueError):
            return 0

    items = sorted(data.get("items", []), key=index_key)
    return [p["status"]["podIP"] for p in items
            if p.get("status", {}).get("podIP")]


def pod_ip(name: str, args) -> str:
    out = kubectl_capture(f"get pod {name} -o jsonpath={{.status.podIP}}", args)
    return (out or "").strip()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def detect_api_port(plan: dict) -> int:
    raw = plan["launch_cmds"].get("prefill", "")
    m = re.search(r"--vllm-start-port\s+(\d+)", raw)
    return int(m.group(1)) if m else 7100


def wait_pods_ready(selector: str, lws_name: str, args, total: int) -> None:
    deadline = time.time() + args.pod_wait_min * 60
    ready = 0
    it = 0
    while time.time() < deadline:
        it += 1
        ready = pod_count_ready(selector, args)
        print(f"[controller] pods ready {ready}/{total}", flush=True)
        # Every ~30s (loop sleeps 15s, so every 2nd pass) and on the final
        # check, dump the pod table + the leader's pod log. That makes per-pod
        # state (Pending / Running / CrashLoopBackOff, node, IP) AND why the
        # leader isn't Ready (e.g. missing Mooncake, bad image) visible in the
        # step log. Other nodes' logs are captured to the artifact on failure
        # via dump_logs.
        if it % 2 == 1 or ready == total:
            table = kubectl_capture(f"get pods -l {selector} -o wide", args)
            if table:
                print(table.rstrip(), flush=True)
            else:
                print(f"(no pods matching {selector} — the LWS controller has "
                      "not created them yet)", flush=True)
            leader = kubectl_capture(f"logs {lws_name}-0 --tail=60", args)
            if leader:
                print(f"--- {lws_name}-0 log (tail 60) ---", flush=True)
                print(leader.rstrip(), flush=True)
            else:
                print(f"(no {lws_name}-0 log yet — pod may not be running)",
                      flush=True)
        if ready == total:
            return
        time.sleep(15)
    raise PipelineError("pods_ready", f"only {ready}/{total} pods Ready after "
                                       f"{args.pod_wait_min}min")


def pod_count_ready(selector: str, args) -> int:
    out = kubectl_capture(f"get pods -l {selector} -o json", args)
    if not out:
        return 0
    ready = 0
    for pod in json.loads(out).get("items", []):
        if pod.get("status", {}).get("phase") != "Running":
            continue
        conds = {c.get("type"): c.get("status")
                 for c in pod.get("status", {}).get("conditions", [])}
        if conds.get("Ready") == "True":
            ready += 1
    return ready


def wait_http(url: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            rc = subprocess.run(["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
                                 url], capture_output=True, text=True, timeout=20)
            if rc.returncode == 0:
                print(f"[controller] {url} ready (HTTP {rc.stdout})", flush=True)
                return
        except subprocess.TimeoutExpired:
            pass
        print(f"[controller] waiting for {url} ...", flush=True)
        time.sleep(10)
    raise PipelineError("service_ready", f"{url} not ready within {timeout_s}s")


def fill_verify(text: str, host: str, port: int) -> str:
    text = (text.replace("<node0_ip>", host)
                .replace("<proxy_node0_ip>", host)
                .replace("<proxy_endpoint>", host))
    text = re.sub(r"<port>", str(port), text)
    # Fail on HTTP >= 400 so a 5xx doesn't count as a passing curl.
    return re.sub(r"^(\s*curl\b)", r"\1 -sf", text, flags=re.M)


def run_verify(plan: dict, endpoint: str, args) -> None:
    host = endpoint.split("//", 1)[1].split(":", 1)[0] if "//" in endpoint else endpoint
    port = plan.get("proxy_port") or (args.proxy_port if args.proxy_command
                                      else detect_api_port(plan))
    for i, cmd in enumerate(plan.get("verify_cmds", [])):
        script = fill_verify(cmd, host, port)
        rc = subprocess.run(["bash", "-c", f"set -eo pipefail\n{script}\n"],
                            capture_output=True, text=True)
        print(f"[controller] verify[{i}] rc={rc.returncode}\n{rc.stdout}\n{rc.stderr}",
              flush=True)
        if rc.returncode != 0:
            raise PipelineError("verify", f"verify[{i}] failed")


def endpoint_host_port(endpoint: str) -> tuple[str, int]:
    """Split an http(s) endpoint into (host, port)."""
    rest = endpoint.split("://", 1)[1] if "://" in endpoint else endpoint
    host, _, port = rest.rpartition(":")
    if not host:
        return rest, 80
    return host, int(port.split("/", 1)[0])


def aisbench_config(plan: dict, endpoint: str, results_dir: Path) -> Path:
    """Render AISBench model configs (general chat + stream chat) pointing at
    the PD service endpoint. Same templates as the proven PR #34 plan."""
    host, port = endpoint_host_port(endpoint)
    model_path = plan.get("model_cache_path", "")
    model = served_model_name(plan.get("role_templates", {}).get("prefill", ""))
    cfg_dir = results_dir / "aisbench-config" / "models"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    tpl_dir = SCRIPT_DIR / "aisbench" / "models"
    for name in ("vllm_api_general_chat.py", "vllm_api_stream_chat.py"):
        tpl = (tpl_dir / name).read_text(encoding="utf-8")
        rendered = (tpl
                    .replace("__RECIPE_MODEL_PATH__", repr(model_path))
                    .replace("__RECIPE_SERVED_MODEL_NAME__", repr(model))
                    .replace("__RECIPE_ENDPOINT_HOST__", repr(host))
                    .replace("__RECIPE_ENDPOINT_PORT__", str(port))
                    .replace("__RECIPE_AISBENCH_MAX_OUT_LEN__", "512"))
        (cfg_dir / name).write_text(rendered, encoding="utf-8")
    return cfg_dir.parent


def aisbench_prepare_dataset(results_dir: Path) -> dict:
    """Place the vendored gsm8k fixture where AISBench's built-in dataset
    configs expect it (AIS_BENCH_DATASETS_CACHE/ais_bench/datasets/gsm8k)."""
    cache_root = results_dir / "aisbench-data"
    target = cache_root / "ais_bench" / "datasets" / "gsm8k"
    target.mkdir(parents=True, exist_ok=True)
    src = SCRIPT_DIR / "aisbench" / "datasets" / "gsm8k"
    for name in ("train.jsonl", "test.jsonl"):
        shutil.copyfile(src / name, target / name)
    return {"AIS_BENCH_DATASETS_CACHE": str(cache_root)}


def _aisbench_number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        token = value.strip().split()[0].replace(",", "") if value.strip() else ""
        try:
            return float(token)
        except ValueError:
            return None
    return None


def _aisbench_flatten(value, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _aisbench_flatten(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _aisbench_flatten(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def aisbench_accuracy_score(directory: Path) -> tuple[float, Path]:
    standard_columns = {"dataset", "version", "metric", "mode", "total_count"}
    for path in sorted(directory.rglob("*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if str(row.get("metric", "")).lower() != "accuracy":
                    continue
                for column, value in row.items():
                    if column not in standard_columns and value not in (None, ""):
                        try:
                            return float(value), path
                        except ValueError:
                            continue
    raise PipelineError("aisbench", f"no accuracy metric found under {directory}")


def aisbench_performance_metrics(directory: Path) -> tuple[dict[str, float], list[Path]]:
    aliases = {
        "request throughput": "request_per_second",
        "output token throughput": "output_token_per_second",
        "e2e latency": "e2e_latency_ms",
        "e2el": "e2e_latency_ms",
        "ttft": "ttft_ms",
        "tpot": "tpot_ms",
    }
    metrics: dict[str, float] = {}
    sources: list[Path] = []
    for path in sorted(directory.rglob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = False
        for key, raw in _aisbench_flatten(value):
            lowered = key.lower()
            for phrase, metric in aliases.items():
                if phrase in lowered and (parsed := _aisbench_number(raw)) is not None:
                    metrics.setdefault(metric, parsed)
                    found = True
        if found:
            sources.append(path)
    for path in sorted(directory.rglob("*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with path.open(newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except (OSError, csv.Error):
            continue
        found = False
        for row in rows:
            parameter = str(row.get("Performance Parameters", "")).lower()
            average = _aisbench_number(row.get("Average"))
            if average is None:
                continue
            for phrase, metric in aliases.items():
                if phrase in parameter:
                    metrics.setdefault(metric, average)
                    found = True
        if found:
            sources.append(path)
    required = {"request_per_second", "output_token_per_second",
                "e2e_latency_ms", "ttft_ms", "tpot_ms"}
    missing = sorted(required - set(metrics))
    if missing:
        raise PipelineError(
            "aisbench",
            f"performance metrics missing under {directory}: {', '.join(missing)}")
    return metrics, list(dict.fromkeys(sources))


def run_aisbench(plan: dict, endpoint: str, results_dir: Path, args) -> bool:
    """Run AISBench accuracy + performance against the PD service endpoint.
    Mirrors the single-node verify-recipe.sh flow (ais_bench installed on the
    runner) with the PR #34 multi-node plan configs/dataset. Returns False
    when ais_bench is not installed (eval skipped, like the single-node flow)."""
    ais_bin = shutil.which("ais_bench")
    if not ais_bin:
        print("[controller] ais_bench not found — skipping AISBench eval "
              "(add the 'Install AISBench' workflow step to enable it)",
              flush=True)
        return False
    cfg_dir = aisbench_config(plan, endpoint, results_dir)
    data_env = aisbench_prepare_dataset(results_dir)
    out_dir = results_dir / "aisbench"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(data_env)

    # Accuracy: general chat against gsm8k, mode all.
    acc_log = out_dir / "accuracy.log"
    acc_cmd = [ais_bin, "--config-dir", str(cfg_dir),
               "--models", "vllm_api_general_chat",
               "--datasets", "gsm8k_gen_0_shot_cot_chat_prompt",
               "--mode", "all",
               "--num-prompts", str(args.aisbench_accuracy_prompts),
               "--dump-eval-details", "--debug"]
    print(f"[controller] aisbench accuracy: {' '.join(acc_cmd)}", flush=True)
    with acc_log.open("w", encoding="utf-8") as logf:
        rc = subprocess.run(acc_cmd, cwd=out_dir, env=env,
                            stdout=logf, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        raise PipelineError("aisbench",
                            f"ais_bench accuracy failed rc={rc} — see {acc_log}")
    score, src = aisbench_accuracy_score(out_dir)
    (out_dir / "accuracy.json").write_text(
        json.dumps({"accuracy": score, "source": src.name,
                    "prompts": args.aisbench_accuracy_prompts}, indent=2),
        encoding="utf-8")
    print(f"[controller] aisbench accuracy={score} ({src.name})", flush=True)

    # Performance: stream chat against gsm8k perf mode.
    perf_log = out_dir / "performance.log"
    perf_cmd = [ais_bin, "--config-dir", str(cfg_dir),
                "--models", "vllm_api_stream_chat",
                "--datasets", "gsm8k_gen_0_shot_cot_str_perf",
                "--mode", "perf", "--summarizer", "default_perf",
                "--num-prompts", str(args.aisbench_perf_prompts),
                "--debug"]
    print(f"[controller] aisbench performance: {' '.join(perf_cmd)}", flush=True)
    with perf_log.open("w", encoding="utf-8") as logf:
        rc = subprocess.run(perf_cmd, cwd=out_dir, env=env,
                            stdout=logf, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        raise PipelineError("aisbench",
                            f"ais_bench performance failed rc={rc} — see {perf_log}")
    metrics, sources = aisbench_performance_metrics(out_dir)
    (out_dir / "performance.json").write_text(
        json.dumps({"metrics": metrics,
                    "sources": [s.name for s in sources],
                    "prompts": args.aisbench_perf_prompts}, indent=2),
        encoding="utf-8")
    print(f"[controller] aisbench performance: {json.dumps(metrics)}", flush=True)
    return True


def served_model_name(template: str) -> str:
    m = re.search(r"--served-model-name\s+(\S+)", template)
    return m.group(1) if m else "default"


def dump_logs(plan: dict, args, results_dir: Path) -> None:
    logs_dir = results_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    total = plan["topology"]["prefill"] + plan["topology"]["decode"]
    lws = plan["lws_name"]
    # Cluster-wide snapshot first: pod phases + recent events explain scheduling
    # failures (Pending / ImagePullBackOff / CrashLoopBackOff) that pod logs
    # alone cannot — mirrors upstream's `kubectl get pods` + `describe pod`
    # diagnostics in _e2e_nightly_multi_node.yaml.
    pods = kubectl_capture("get pods -o wide", args)
    (logs_dir / "pods.txt").write_text(pods or "no pods", encoding="utf-8")
    events = kubectl_capture("get events --sort-by=.lastTimestamp", args)
    (logs_dir / "events.txt").write_text(events or "no events", encoding="utf-8")
    print("[controller] dumped logs/pods.txt + logs/events.txt", flush=True)
    for i in range(total):
        name = f"{lws}-0" if i == 0 else f"{lws}-0-{i}"
        out = kubectl_capture(f"logs {name} --tail=200", args)
        (logs_dir / f"node-{i}.log").write_text(out or "no logs", encoding="utf-8")
        desc = kubectl_capture(f"describe pod {name}", args)
        (logs_dir / f"node-{i}.describe.txt").write_text(
            desc or "no describe", encoding="utf-8")
        print(f"[controller] dumped logs/node-{i}.log + node-{i}.describe.txt",
              flush=True)


def cleanup(plan: dict, args) -> bool:
    ok = True
    lws = plan["lws_name"]
    cm = f"recipe-entry-{plan['run_id']}"
    ensure_kubeconfig()
    exe = kubectl_binary()
    for res in (f"lws {lws}", f"configmap {cm}"):
        rc = subprocess.run([exe, "delete", *res.split(), "-n", args.namespace,
                             "--ignore-not-found"], capture_output=True, text=True)
        if rc.returncode != 0:
            ok = False
            print(f"[controller] cleanup failed for {res}: {rc.stderr}", file=sys.stderr)
    print("[controller] cleanup done", flush=True)
    return ok


def write_summary(plan: dict, stages: Stages, results_dir: Path, status: str) -> None:
    summary = {
        "recipe": plan["recipe"],
        "scenario": plan["scenario"],
        "topology": plan["topology"],
        "status": status,
        "stages": stages.map,
        "fail_stage": stages.failed,
        "endpoint": plan.get("endpoint", ""),
    }
    (results_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[controller] summary written to {results_dir / 'summary.json'}")


def run_pipeline(plan: dict, args, stages: Stages, results_dir: Path) -> None:
    node_entry_src = (SCRIPT_DIR / "node_entry.py").read_text(encoding="utf-8")
    cm = render_configmap(plan, node_entry_src, args)
    lws = render_lws(plan, args)
    (results_dir / "configmap.yaml").write_text(
        yaml.safe_dump(cm, sort_keys=False), encoding="utf-8")
    (results_dir / "lws.yaml").write_text(lws, encoding="utf-8")

    # LWS pods go in the runner's own namespace (the SA has no cluster-scope
    # permissions, so we cannot create a dedicated namespace).
    print(f"[controller] applying to namespace {args.namespace}", flush=True)
    kubectl(f"apply -f {results_dir / 'configmap.yaml'}", args, stage="apply")
    kubectl(f"apply -f {results_dir / 'lws.yaml'}", args, stage="apply")
    stages.ok("apply")

    lws_name = plan["lws_name"]
    selector = f"leaderworkerset.sigs.k8s.io/name={lws_name}"
    total = plan["topology"]["prefill"] + plan["topology"]["decode"]
    wait_pods_ready(selector, lws_name, args, total)
    stages.ok("pods_ready")

    prefill_ip = pod_ip(f"{lws_name}-0", args)
    api_port = detect_api_port(plan)

    proxy_proc = None
    proxy_cmd = args.proxy_command or plan.get("proxy_command") or ""
    if proxy_cmd:
        ips = pod_ips(selector, args)
        pc = proxy_cmd
        for i, ip in enumerate(ips):
            pc = pc.replace(f"<NODE_{i}_IP>", ip)
        pc = pc.replace("<node0_ip>", prefill_ip).replace("<proxy_node0_ip>", prefill_ip)
        prefill_eps = ",".join(f"http://{ip}:{api_port}"
                               for ip in ips[: plan["topology"]["prefill"]])
        decode_eps = ",".join(f"http://{ip}:{api_port}"
                              for ip in ips[plan["topology"]["prefill"]:])
        pc = pc.replace("<prefill_endpoints>", prefill_eps)
        pc = pc.replace("<decode_endpoints>", decode_eps)
        pc = fill_dotted_ips(pc, ips)
        # A bare proxy script name isn't on PATH inside the pod — resolve it to
        # the mooncake image's vllm-ascend checkout (keep /opt/vllm-ascend).
        pc = re.sub(r"\bpython\s+([A-Za-z0-9_.-]+\.py)\b",
                    "python /opt/vllm-ascend/examples/disaggregated_prefill_v1/"
                    "load_balance_proxy_server_example.py", pc)
        m = re.search(r"--port\s+(\d+)", pc)
        proxy_port = int(m.group(1)) if m else args.proxy_port
        cmd = (f"kubectl exec {lws_name}-0 -n {args.namespace} "
               f"-- bash -lc {shlex.quote(pc)}")
        print(f"[controller] starting proxy (port {proxy_port}): {cmd}", flush=True)
        proxy_proc = subprocess.Popen(cmd, shell=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        endpoint = f"http://{prefill_ip}:{proxy_port}"
        plan["proxy_port"] = proxy_port
    else:
        endpoint = f"http://{prefill_ip}:{api_port}"

    plan["endpoint"] = endpoint
    print(f"[controller] verifying against {endpoint}", flush=True)

    wait_http(f"{endpoint}/v1/models", args.service_timeout_s)
    stages.ok("service_ready")

    run_verify(plan, endpoint, args)
    stages.ok("verify")

    if args.run_eval:
        if run_aisbench(plan, endpoint, results_dir, args):
            stages.ok("aisbench")
        else:
            stages.map["aisbench"] = "skipped"


def main() -> int:
    args = parse_args()
    stages = Stages()
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        plan = parse_recipe(args)
        node_entry_src = (SCRIPT_DIR / "node_entry.py").read_text(encoding="utf-8")
        (results_dir / "plan.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        (results_dir / "configmap.yaml").write_text(
            yaml.safe_dump(render_configmap(plan, node_entry_src, args),
                           sort_keys=False), encoding="utf-8")
        (results_dir / "lws.yaml").write_text(
            render_lws(plan, args), encoding="utf-8")
        print("[controller] DRY-RUN OK — plan + LWS + ConfigMap rendered; "
              "no kubectl invoked", flush=True)
        return 0

    try:
        plan = parse_recipe(args)
    except PipelineError as e:
        print(f"[controller] FAIL: {e}", file=sys.stderr)
        return 1
    (results_dir / "plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    stages.ok("extract")

    status = "pass"
    try:
        run_pipeline(plan, args, stages, results_dir)
    except PipelineError as e:
        status = "fail"
        stages.fail(e.stage, str(e))
        dump_logs(plan, args, results_dir)
    except Exception as e:  # noqa: BLE001 — surface any failure as run fail
        status = "fail"
        stages.fail("run", repr(e))
        dump_logs(plan, args, results_dir)
    finally:
        if not cleanup(plan, args):
            stages.fail("cleanup", "kubectl delete failed")

    write_summary(plan, stages, results_dir, status)
    print(f"[controller] {'PASS' if status == 'pass' else 'FAIL'}", flush=True)
    return 0 if status == "pass" else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recipe", required=True,
                   help="Recipe YAML path (relative to repo root or absolute)")
    p.add_argument("--hw-filter", default="A2", help="NPU filter, e.g. A2 / A3")
    p.add_argument("--deployment-filter", default="PD",
                   help="Scenario deployment type to select (case-insensitive "
                        "substring; 'PD' matches both languages)")
    p.add_argument("--strategy", default="",
                   help="Exact scenario strategy to select (pd_cluster / "
                        "multi_node_dp / single_node_A2 / ...); overrides the "
                        "hw/deployment filters. The workflow passes this when "
                        "routing by scenario tags.")
    p.add_argument("--prefill-nodes", type=int, default=0,
                   help="Prefill node count (0 = auto-derive from recipe DP config)")
    p.add_argument("--decode-nodes", type=int, default=0,
                   help="Decode node count (0 = auto-derive from recipe DP config)")
    p.add_argument("--npu-per-node", type=int, default=0,
                   help="Ascend NPU cards per pod (0 = auto-derive = "
                        "dp-size-local × tp-size; also forces 1 pod/node)")
    p.add_argument("--image", default=DEFAULT_IMAGE)
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p.add_argument("--pvc-name", dest="pvc_name",
                   default="vllm-ascend-vllm-ascend-recipes-gy001",
                   help="Shared RWX PVC for the model cache (mounted at /root/.cache)")
    p.add_argument("--chip", default=DEFAULT_CHIP,
                   help="node.kubernetes.io/npu.chip.name affinity value")
    p.add_argument("--run-id", default=str(int(time.time())))
    p.add_argument("--proxy-command", default=os.environ.get("PROXY_COMMAND", ""),
                   help="Optional Mooncake proxy launch command (runs on prefill node 0). "
                        "Supports <NODE_i_IP>, <node0_ip>, <prefill_endpoints>, "
                        "<decode_endpoints> placeholders.")
    p.add_argument("--proxy-port", type=int, default=8088)
    p.add_argument("--run-eval", action="store_true",
                   help="Run AISBench accuracy + performance after verify")
    p.add_argument("--aisbench-accuracy-prompts", type=int, default=8,
                   help="Number of gsm8k prompts for the AISBench accuracy run")
    p.add_argument("--aisbench-perf-prompts", type=int, default=50,
                   help="Number of prompts for the AISBench performance run")
    p.add_argument("--pod-wait-min", type=int, default=20)
    p.add_argument("--service-timeout-s", type=int, default=3600,
                   help="How long to poll the prefill endpoint for /v1/models")
    p.add_argument("--results-dir", default="/tmp/multinode-results")
    p.add_argument("--dry-run", action="store_true",
                   help="Only render plan/LWS/ConfigMap; no kubectl")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
