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
import json
import os
import re
import shlex
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

    missing_tpl = [k for k in ("prefill", "decode") if k not in plan["role_templates"]]
    missing_launch = [k for k in ("prefill", "decode") if k not in plan["launch_cmds"]]
    if missing_tpl:
        raise PipelineError("extract", f"role template missing for: {missing_tpl}")
    if missing_launch:
        raise PipelineError("extract", f"launch command missing for: {missing_launch}")

    # Topology: an explicit input wins; 0 = auto-derive from the launch command
    # (nodes = dp-size // dp-size-local, npu-per-node = dp-size-local × tp-size).
    if plan["topology"]["prefill"] == 0 or plan["topology"]["decode"] == 0 \
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


# ---------------------------------------------------------------------------
# LWS / ConfigMap rendering
# ---------------------------------------------------------------------------

def _volumes(npu_per_node: int, entry_cm: str, pvc_name: str = "") -> list[dict]:
    volumes = []
    for i in range(npu_per_node):
        volumes.append({"name": f"davinci{i}",
                        "hostPath": {"path": f"/dev/davinci{i}", "type": "CharDevice"}})
    for name, path in (("davinci-manager", "/dev/davinci_manager"),
                       ("devmm-svm", "/dev/devmm_svm"),
                       ("hisi-hdc", "/dev/hisi_hdc")):
        volumes.append({"name": name, "hostPath": {"path": path, "type": "CharDevice"}})
    for name, path in (("dcmi", "/usr/local/dcmi"),
                       ("hccn-tool", "/usr/local/Ascend/driver/tools/hccn_tool"),
                       ("npu-smi", "/usr/local/bin/npu-smi"),
                       ("driver-lib64", "/usr/local/Ascend/driver/lib64"),
                       ("driver-version", "/usr/local/Ascend/driver/version.info"),
                       ("ascend-install-info", "/etc/ascend_install.info"),
                       ("hccn-conf", "/etc/hccn.conf")):
        volumes.append({"name": name, "hostPath": {"path": path}})
    # Model weights: a node-local hostPath only works if every schedulable node
    # has them. PR #34 mounts the shared RWX cache PVC at /root/.cache — do the
    # same (pvc_name defaults to the cluster's shared cache volume).
    if pvc_name:
        volumes.append({"name": "model-cache",
                        "persistentVolumeClaim": {"claimName": pvc_name}})
    else:
        volumes.append({"name": "model-cache",
                        "hostPath": {"path": "/root/.cache/modelscope"}})
    volumes.append({"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "512Gi"}})
    volumes.append({"name": "workdir", "emptyDir": {}})
    volumes.append({"name": "entry", "configMap": {"name": entry_cm}})
    return volumes


def _volume_mounts(npu_per_node: int) -> list[dict]:
    mounts = [{"name": f"davinci{i}", "mountPath": f"/dev/davinci{i}"}
              for i in range(npu_per_node)]
    mounts += [
        {"name": "davinci-manager", "mountPath": "/dev/davinci_manager"},
        {"name": "devmm-svm", "mountPath": "/dev/devmm_svm"},
        {"name": "hisi-hdc", "mountPath": "/dev/hisi_hdc"},
        {"name": "dcmi", "mountPath": "/usr/local/dcmi"},
        {"name": "hccn-tool", "mountPath": "/usr/local/Ascend/driver/tools/hccn_tool"},
        {"name": "npu-smi", "mountPath": "/usr/local/bin/npu-smi"},
        {"name": "driver-lib64", "mountPath": "/usr/local/Ascend/driver/lib64"},
        {"name": "driver-version", "mountPath": "/usr/local/Ascend/driver/version.info"},
        {"name": "ascend-install-info", "mountPath": "/etc/ascend_install.info"},
        {"name": "hccn-conf", "mountPath": "/etc/hccn.conf"},
        {"name": "model-cache", "mountPath": "/root/.cache"},
        {"name": "shm", "mountPath": "/dev/shm"},
        {"name": "workdir", "mountPath": "/run/recipe-ci"},
        {"name": "entry", "mountPath": "/scripts"},
    ]
    return mounts


def _pod_spec(plan: dict, args, entry_cm: str) -> dict:
    npu = plan.get("npu_per_node") or args.npu_per_node
    lws = plan["lws_name"]
    return {
        "metadata": {"labels": {"multinode-lws": lws}},
        "hostNetwork": True,
        # hostNetwork pods default to dnsPolicy: Default (the node's
        # resolv.conf), which has no cluster search domains -> the LWS headless
        # service DNS (<pod>.<group>.<ns>.svc.cluster.local) would NOT resolve
        # and node_entry could not find its peers. ClusterFirstWithHostNet
        # routes hostNetwork-pod DNS through CoreDNS with the pod's search
        # domains, fixing peer resolution.
        "dnsPolicy": "ClusterFirstWithHostNet",
        # No pod-level restartPolicy: the CCE LWS addon (cceaddon-lws-
        # controller-manager) creates a StatefulSet per subgroup, and K8s
        # rejects StatefulSet pod templates with restartPolicy != "Always".
        # Omitting it leaves the default "Always"; failed-pod diagnosis still
        # works (CrashLoopBackOff pods are loggable).
        # SOFT affinity for the NPU chip: the pod already requests
        # NPU_RESOURCE (huawei.com/ascend-1980), which is what actually pins it
        # to NPU-capable nodes. A *required* nodeAffinity on the chip label
        # used to block scheduling entirely when nodes didn't carry
        # node.kubernetes.io/npu.chip.name={args.chip}; as a preference it
        # still favors the target chip but lets the pod land on any NPU node.
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
            # PD separation REQUIRES prefill and decode on different nodes (they
            # use the same DP RPC port 12321 — co-locating them gives "Address
            # already in use"). PR #34 pins 1 pod/node via podAntiAffinity; do
            # the same on a shared LWS label + kubernetes.io/hostname.
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
                # NOTE: VLLM_USE_MODELSCOPE must NOT be set here. With it, vllm
                # passes the model path to modelscope's snapshot_download, which
                # treats the absolute path as a REMOTE model id and 404s. The
                # weights are pre-downloaded as a plain local dir under
                # /root/.cache/modelscope/hub/models/Eco-Tech/<model>, which vllm
                # loads directly without the flag.
                # Mooncake runtime .so lives under site-packages/mooncake; the
                # mooncake-enabled image bakes this via ENV too, this is insurance.
                {"name": "LD_LIBRARY_PATH",
                 "value": "/usr/local/lib64/python3.12/site-packages/mooncake"},
            ],
            "securityContext": {"privileged": True},
            "resources": {
                "limits": {NPU_RESOURCE: npu,
                           "memory": f"{npu * 64}Gi",
                           "ephemeral-storage": "100Gi"},
                # CPU request scales with NPU count (4/NPU) — a hardcoded 125
                # (and later 8/NPU) made pods Unschedulable on 910B4 nodes that
                # were short on free CPU; 4/NPU fits 4-card nodes (tp*dp=4).
                # Memory also scales (64Gi/NPU): a flat 512Gi request was
                # Unschedulable on nodes that don't have 512Gi free.
                "requests": {NPU_RESOURCE: npu,
                             "cpu": str(npu * 4),
                             "memory": f"{npu * 64}Gi",
                             "ephemeral-storage": "100Gi"},
            },
            "volumeMounts": _volume_mounts(npu),
        }],
        "volumes": _volumes(npu, entry_cm, getattr(args, "pvc_name", "")),
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
    pod_spec = yaml.safe_dump(_pod_spec(plan, args, entry_cm), sort_keys=False).rstrip()
    tpl = (SCRIPT_DIR / "lws.yaml.jinja2").read_text(encoding="utf-8")
    values = {
        "LWS_NAME": plan["lws_name"],
        "NAMESPACE": args.namespace,
        "SIZE": str(size),
        "LEADER_SPEC": _indent(pod_spec, 8),
        "WORKER_SPEC": _indent(pod_spec, 8),
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


def run_eval(plan: dict, endpoint: str, results_dir: Path) -> None:
    template = plan.get("role_templates", {}).get("prefill", "")
    model = served_model_name(template)
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "Who are you?"}],
                       "max_tokens": 32, "temperature": 0})
    out = results_dir / "eval-response.json"
    rc = subprocess.run(["curl", "-sf", "-o", str(out), "-w", "%{time_total}",
                         "-X", "POST", f"{endpoint}/v1/chat/completions",
                         "-H", "Content-Type: application/json", "-d", body],
                        capture_output=True, text=True, timeout=300)
    if rc.returncode != 0:
        raise PipelineError("eval", f"eval curl failed: {rc.stderr}")
    print(f"[controller] eval OK in {rc.stdout}s (response saved)", flush=True)


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
        run_eval(plan, endpoint, results_dir)
        stages.ok("eval")


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
                   help="Run a lightweight latency check after verify")
    p.add_argument("--pod-wait-min", type=int, default=20)
    p.add_argument("--service-timeout-s", type=int, default=3600,
                   help="How long to poll the prefill endpoint for /v1/models")
    p.add_argument("--results-dir", default="/tmp/multinode-results")
    p.add_argument("--dry-run", action="store_true",
                   help="Only render plan/LWS/ConfigMap; no kubectl")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
