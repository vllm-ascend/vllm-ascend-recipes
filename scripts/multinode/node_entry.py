#!/usr/bin/env python3
"""node_entry.py — in-pod entrypoint for the multi-node PD recipe verify pipeline.

Every LWS pod runs this script. It picks its role (Prefill / Decode) from
``LWS_WORKER_INDEX``, materialises the role's ``run_dp_template.sh`` and the
recipe's ``launch_online_dp.py`` from ``plan.json`` (mounted from a ConfigMap),
fills in the runtime values a human would normally edit by hand (NIC, node IP,
dp-address, dp-rank-start, kv_port, engine_id — plan.md §九 whitelist), then
executes ``python launch_online_dp.py ...`` in the foreground.

No shared storage is needed: peer node IPs are resolved through the LWS
headless services (``<lws>-leader`` / ``<lws>-worker``). Pods run with
``hostNetwork: true``, so every pod IP is a physical node IP.

Exit code == launch_online_dp.py exit code. Pods are not restarted
(``restartPolicy: None``); the controller diagnoses failures via ``kubectl
logs``.
"""

import json
import os
import re
import socket
import subprocess
import sys

WORKDIR = os.environ.get("MULTINODE_WORKDIR", "/run/recipe-ci")
PLAN_PATH = os.environ.get("MULTINODE_PLAN", "/scripts/plan.json")

# vLLM config flags CI must never silently alter (plan.md §九). We don't reject
# their *presence* in a recipe — they are the point of the recipe — we reject
# any *change* between the recipe text and the rendered script.
FORBIDDEN_FLAGS = (
    "tensor-parallel-size", "max-model-len", "max-num-seqs",
    "max-num-batched-tokens", "quantization", "compilation-config",
    "additional-config", "speculative-config", "kv-connector",
    "enable-expert-parallel", "gpu-memory-utilization",
)


def log(msg: str) -> None:
    print(f"[node_entry:{os.getpid()}] {msg}", flush=True)


def get_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def lws_identity() -> int:
    """Return the worker index (leader=0). Prefer the LWS-injected env var;
    fall back to parsing the pod hostname (``<lws>-0`` / ``<lws>-0-3``)."""
    idx = get_env_int("LWS_WORKER_INDEX", -1)
    if idx >= 0:
        return idx
    m = re.search(r"-(\d+)$", socket.gethostname())
    return int(m.group(1)) if m else 0


def resolve_ip(dns: str) -> str | None:
    try:
        return socket.getaddrinfo(dns, None, socket.AF_INET)[0][4][0]
    except (socket.gaierror, OSError):
        return None


def peer_dns_candidates(lws: str, ns: str, idx: int) -> list[str]:
    """Build candidate DNS names for peer ``idx``.

    Primary path (aligned with upstream tests/e2e/nightly/multi_node/utils.py):
    parse the LWS-injected ``LWS_LEADER_ADDRESS`` env var — ``<leader>.<group>.
    <ns>.<...>`` — and derive every worker name from it, so we never have to
    guess the headless-service suffix. ``leader`` here is the leader pod name
    (``<lws>-0``), so worker ``idx`` is ``<lws>-0-<idx>`` in the same group.

    Fall back to the previous defensive name variants when the env var is
    absent (bare pod templates that don't set it)."""
    candidates: list[str] = []
    leader_dns = os.environ.get("LWS_LEADER_ADDRESS", "")
    if leader_dns:
        parts = leader_dns.split(".")
        if len(parts) >= 3:
            leader_name, group_name, namespace = parts[0], parts[1], parts[2]
            if idx == 0:
                # The leader's own address — already correct (FQDN or short).
                candidates = [leader_dns]
            else:
                base = f"{leader_name}-{idx}.{group_name}.{namespace}"
                candidates = [base, f"{base}.svc.cluster.local"]
    if not candidates:
        base = lws + "-0" if idx == 0 else f"{lws}-0-{idx}"
        svcs = ["leader", "worker", ""]
        candidates = [f"{base}.{lws}-{svc}.{ns}.svc.cluster.local".replace("--", "-")
                      for svc in svcs]
    return candidates


def resolve_peer_ips(lws: str, ns: str, size: int, local_idx: int = -1) -> list[str]:
    ips = []
    for i in range(size):
        if i == local_idx:
            # Own pod: hostNetwork means pod IP == node IP — take it locally via
            # `hostname -I` instead of DNS. The headless-service DNS for the
            # pod's own name can lag (the pod isn't in the endpoints until it's
            # Ready), and resolving itself over DNS deadlocked the leader in a
            # CrashLoop (never Ready -> never published -> never resolvable).
            ips.append(own_ip_fallback())
            continue
        ip = None
        for dns in peer_dns_candidates(lws, ns, i):
            ip = resolve_ip(dns)
            if ip:
                break
        if ip is None:
            raise RuntimeError(
                f"could not resolve peer {i}; tried {peer_dns_candidates(lws, ns, i)}")
        ips.append(ip)
    return ips


def own_ip_fallback() -> str:
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).split()
        return out[0] if out else "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def resolve_nic(ip: str) -> str:
    """Find the NIC that has ``ip`` assigned, via ``ip -o -4 addr`` (the recipe's
    ``nic_name`` must match the NIC carrying the node's HCCL/IP — this is what a
    user would look up with ``ip a``). Fall back to the default-route NIC (via
    ``ip`` if present, else /proc/net/route), then ``eth0``."""
    try:
        out = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True)
        for line in out.splitlines():
            parts = line.split()
            # e.g. "2: enp67s0f0np0  inet 10.20.1.1/24 brd ... scope global ..."
            if len(parts) >= 4 and parts[1] != "lo" and parts[3].startswith(ip + "/"):
                return parts[1]
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["sh", "-c", "ip -o -4 route show to default | awk '{print $5}' | head -n1"],
            text=True,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    # No `ip` binary in this image: read the default route's interface from
    # /proc/net/route (Iface Destination ... — default route has 00000000).
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "00000000":
                    return parts[0]
    except Exception:
        pass
    return "eth0"


# ---------------------------------------------------------------------------
# Runtime fill helpers (plan.md §九 whitelist — nothing else may change)
# ---------------------------------------------------------------------------

def fill_nic_name(text: str, nic: str) -> str:
    return re.sub(r'nic_name\s*=\s*"[^"]*"', f'nic_name="{nic}"', text)


def fill_local_ip(text: str, own_ip: str) -> str:
    """Replace manual IP placeholders (``local_ip=xx.xx.xx.1`` / ``141.xx.xx.1``);
    leave computed ``local_ip=$(hostname -I ...)`` forms untouched."""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*local_ip\s*=", line)
        if m and "hostname" not in line:
            val = line.split("=", 1)[1].strip().strip("'\"")
            if re.fullmatch(r"(?:\d+\.)?(?:[xX]+\.)+[\dXx]+", val):
                line = line.split("=", 1)[0] + f"={own_ip}"
        out.append(line)
    return "\n".join(out)


def fill_kv_config(text: str, group_offset: int) -> str:
    """Bump ``kv_port`` / ``engine_id`` inside ``--kv-transfer-config`` per node
    within a role group. Recipe instructs: engine_id starts at the group base
    and increments by 1; kv_port is unique per engine, stepping by 100."""
    m = re.search(r'"kv_port"\s*:\s*"(\d+)"', text)
    if m:
        new = int(m.group(1)) + group_offset * 100
        text = re.sub(r'"kv_port"\s*:\s*"\d+"', f'"kv_port": "{new}"', text, count=1)
    m = re.search(r'"engine_id"\s*:\s*"(\d+)"', text)
    if m:
        new = int(m.group(1)) + group_offset
        text = re.sub(r'"engine_id"\s*:\s*"\d+"', f'"engine_id": "{new}"', text, count=1)
    return text


def forbidden_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if any(f in ln for f in FORBIDDEN_FLAGS)]


def render_role_script(role: str, template: str, own_ip: str, nic: str,
                       group_offset: int, model_cache: str) -> str:
    before = forbidden_lines(template)
    text = fill_nic_name(template, nic)
    text = fill_local_ip(text, own_ip)
    text = fill_kv_config(text, group_offset)
    if model_cache:
        # Bracketed forms first (a bare replacement would corrupt the brackets),
        # then the bare `your_model_path` convention used by the Qwen recipes.
        text = text.replace("<your_model_path>", model_cache)
        text = text.replace("<MODEL_PATH>", model_cache)
        text = text.replace("your_model_path", model_cache)
    if forbidden_lines(text) != before:
        raise RuntimeError(
            f"{role} template: a forbidden vLLM flag was modified by the runtime fill")
    return text


def extract_launch_args(raw: str) -> str:
    """Strip a leading ``python launch_online_dp.py`` (and any comment lines)."""
    m = re.search(r"launch_online_dp\.py\s*(.*)", raw, re.DOTALL)
    return m.group(1).strip() if m else raw.strip()


def render_launch_args(role: str, args: str, own_ip: str, decode_master: str,
                       group_offset: int) -> str:
    text = args
    if role == "prefill":
        # Each prefill node is its own DP master (recipe: dp-address = own IP).
        text = re.sub(r"--dp-address\s+\S+", f"--dp-address {own_ip}", text, count=1)
    else:
        text = re.sub(r"--dp-address\s+\S+", f"--dp-address {decode_master}", text, count=1)
        m = re.search(r"--dp-size-local\s+(\d+)", text)
        step = int(m.group(1)) if m else 1
        rank = group_offset * step
        text = re.sub(r"--dp-rank-start\s+\S+", f"--dp-rank-start {rank}", text, count=1)
    return text


def served_model_name(template: str) -> str:
    m = re.search(r"--served-model-name\s+(\S+)", template)
    return m.group(1) if m else "default"


def check_mooncake() -> bool:
    """The MooncakeHybridConnector (used by PD scenarios) imports
    ``mooncake.engine.TransferEngine`` at runtime. Mooncake must be baked into
    the image (see scripts/multinode/mooncake/); if it is missing, fail fast
    with actionable guidance instead of letting vllm serve crash 30min later."""
    import glob
    import sys
    # The real install (v0.23.0rc1) lives under
    # /usr/local/python3.12.13/lib/python3.12/site-packages/mooncake, which a
    # /usr/local/lib*/... glob misses — search the common layouts, add the
    # package dir to sys.path and its .so dir to LD_LIBRARY_PATH.
    patterns = [
        "/usr/local/lib*/python*/site-packages/mooncake",
        "/usr/local/python*/lib/python*/site-packages/mooncake",
        "/usr/lib/python*/site-packages/mooncake",
        "/usr/local/lib*/python*/dist-packages/mooncake",
    ]
    hits: list[str] = []
    for pat in patterns:
        hits.extend(glob.glob(pat))
    # Diagnostic + wider scan: if the targeted globs miss, walk the tree for
    # any mooncake package dir so we can see where it actually lives.
    if not hits:
        for root in ("/usr/local", "/usr/lib", "/opt"):
            for dirpath, dirnames, _ in os.walk(root):
                if "mooncake" in dirnames:
                    p = os.path.join(dirpath, "mooncake")
                    if p not in hits:
                        hits.append(p)
                        log(f"found mooncake by walk: {p}")
                dirnames[:] = [d for d in dirnames if d != "mooncake"]
    for h in hits:
        parent = os.path.dirname(h)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        os.environ["LD_LIBRARY_PATH"] = (h + ":"
                                         + os.environ.get("LD_LIBRARY_PATH", ""))
        log(f"mooncake candidate: {h}")

    def _try_import() -> bool:
        try:
            from mooncake.engine import TransferEngine  # type: ignore  # noqa: F401
            log("mooncake.engine import OK")
            return True
        except Exception as exc:
            log(f"mooncake import failed: {exc!r}")
            return False

    if _try_import():
        return True
    # Only pip-install when the mooncake package is genuinely absent. The
    # baked-in image package must NOT be replaced at runtime: pulling a
    # different mooncake-transfer-engine-npu build over it has caused glibc
    # heap corruption ("corrupted size vs. prev_size") in the vllm engine.
    if hits:
        log("baked-in mooncake import failed (env issue) — NOT pip-installing; "
            "vllm serve will report whether the runtime is actually usable")
        return False
    log("baked-in mooncake import failed — trying pip install "
        "mooncake-transfer-engine-npu")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "mooncake-transfer-engine-npu"], timeout=600)
        source_ascend_env()
        if _try_import():
            log("mooncake installed via pip + import OK")
            return True
    except Exception as exc:
        log(f"mooncake pip install failed: {exc!r}")
    return False


def source_ascend_env() -> None:
    """Source the Ascend toolkit env — same set as PR #34's run.sh, which is
    the working reference for the mooncake path:
      * /usr/local/Ascend/ascend-toolkit/set_env.sh
      * /usr/local/Ascend/nnal/atb/set_env.sh   (ATB; its lib dirs carry
        libascend_hal.so, which mooncake.engine needs)
    Then also collect every Ascend lib dir holding libascend*.so so the whole
    mooncake .so chain resolves on LD_LIBRARY_PATH."""
    setups = ("/usr/local/Ascend/ascend-toolkit/set_env.sh",
              "/usr/local/Ascend/nnal/atb/set_env.sh",
              "/usr/local/Ascend/ascend-toolkit/bin/setenv.bash")
    for setup in setups:
        if not os.path.isfile(setup):
            continue
        try:
            proc = subprocess.run(
                ["bash", "-c", f"source {setup} >/dev/null 2>&1 && env"],
                capture_output=True, text=True, timeout=60)
        except Exception:
            continue
        for line in proc.stdout.splitlines():
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key in ("LD_LIBRARY_PATH", "PYTHONPATH", "PATH", "ASCEND_HOME_PATH",
                       "ASCEND_TOOLKIT_HOME", "ASCEND_OPPER_PATH"):
                os.environ[key] = val
        log(f"sourced Ascend env from {setup}")
    # Version-specific device lib dirs (e.g. cann-9.0.1/aarch64-linux/lib64/
    # device/lib64) hold libascend_hal.so which set_env.sh does not add.
    ldp = os.environ.get("LD_LIBRARY_PATH", "")
    seen = set(ldp.split(":"))
    for root in ("/usr/local/Ascend",):
        for dp, _dn, fn in os.walk(root):
            if any(f.startswith("libascend") and f.endswith(".so") for f in fn):
                if dp not in seen:
                    ldp = dp + ":" + ldp
                    seen.add(dp)
    os.environ["LD_LIBRARY_PATH"] = ldp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    idx = lws_identity()
    with open(PLAN_PATH, encoding="utf-8") as f:
        plan = json.load(f)

    topology = plan.get("topology", {})
    prefill_nodes = int(topology.get("prefill", 1))
    decode_nodes = int(topology.get("decode", 1))
    role = "prefill" if idx < prefill_nodes else "decode"
    group_offset = idx if role == "prefill" else idx - prefill_nodes

    lws = plan.get("lws_name", "recipe-lws")
    ns = plan.get("namespace", "vllm-project")
    size = prefill_nodes + decode_nodes

    log(f"index={idx} size={size} role={role} group_offset={group_offset}")

    peer_ips = resolve_peer_ips(lws, ns, size, local_idx=idx)
    own_ip = peer_ips[idx] if idx < len(peer_ips) else own_ip_fallback()
    nic = resolve_nic(own_ip)
    log(f"own_ip={own_ip} nic={nic} peers={peer_ips}")

    node_dir = os.path.join(WORKDIR, f"node-{idx}")
    os.makedirs(node_dir, exist_ok=True)

    # Multi-node DP: each pod runs its own per-node template (node 0, 1, ...);
    # PD uses the shared prefill/decode role templates.
    node_templates = plan.get("node_templates") or []
    if node_templates:
        template = node_templates[idx] if idx < len(node_templates) else ""
    else:
        template = plan.get("role_templates", {}).get(role, "")
    if not template:
        log(f"no role template for {role}")
        return 1
    if "Mooncake" in template:
        source_ascend_env()  # libascendcl.so must be on LD_LIBRARY_PATH first
        if not check_mooncake():
            # Non-fatal, matching PR #34's run.sh: it sets LD_LIBRARY_PATH and
            # lets vllm serve import mooncake at startup (vllm initialises the
            # runtime itself and can succeed where a bare `import
            # mooncake.engine` here fails on the .so chain). Fail-fast only
            # blocked ever reaching the real test.
            log("WARNING mooncake check failed, but continuing — vllm serve "
                "will report whether the runtime is actually usable")
    script = render_role_script(role, template, own_ip, nic, group_offset,
                                plan.get("model_cache_path") or "")
    with open(os.path.join(node_dir, "run_dp_template.sh"), "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(os.path.join(node_dir, "run_dp_template.sh"), 0o755)

    launch_py = plan.get("launch_online_dp_py", "")
    if launch_py:
        with open(os.path.join(node_dir, "launch_online_dp.py"), "w", encoding="utf-8") as f:
            f.write(launch_py)
        os.chmod(os.path.join(node_dir, "launch_online_dp.py"), 0o755)

    raw_cmd = plan.get("launch_cmds", {}).get(role, "")
    if not raw_cmd:
        log(f"no launch command for role {role}")
        return 1
    decode_master = peer_ips[prefill_nodes] if role == "decode" else own_ip
    launch_args = render_launch_args(role, extract_launch_args(raw_cmd),
                                     own_ip, decode_master, group_offset)

    launch_sh = os.path.join(node_dir, "launch.sh")
    with open(launch_sh, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write("set -eo pipefail\n")
        f.write(". /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || true\n")
        f.write(". /usr/local/Ascend/nnal/atb/set_env.sh 2>/dev/null || true\n")
        f.write(f"cd {node_dir}\n")
        f.write(f"exec python launch_online_dp.py {launch_args}\n")
    os.chmod(launch_sh, 0o755)

    log(f"launching: python launch_online_dp.py {launch_args}")
    # Inherit stdout/stderr so `kubectl logs` sees everything.
    proc = subprocess.run(["bash", launch_sh], cwd=node_dir)
    log(f"launch_online_dp.py exited rc={proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
