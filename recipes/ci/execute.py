#!/usr/bin/env python3
#
# Copyright (c) 2026 Huawei Technologies Co., Ltd. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# This file is a part of the vllm-ascend project.
#
"""Run one generated recipe CI combo on an NPU runner (template section 7).

Consumes the artifacts produced by ``recipes/ci/generate.py`` on the NPU job:

1. prechecks: visible NPU count, container image tag vs the recipe image,
   weights cache (main model + speculative draft model),
2. run ``run.sh`` (engine) and capture ``serve.log``,
3. poll ``/v1/models`` until ready, start companions (vllm-router etc.),
4. smoke test ``POST /v1/chat/completions``,
5. teardown and write ``result.json`` (pass / fail / skip).

Exit code: 0 for pass/skip, 1 for fail. Stdlib only - runs inside the recipe
container image without extra pip installs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

POLL_INTERVAL_S = 5
HTTP_TIMEOUT_S = 10
SMOKE_TIMEOUT_S = 120
SMOKE_MAX_TOKENS = 8
COMPANION_READY_TIMEOUT_S = 600
TEARDOWN_WAIT_S = 30
LOG_TAIL_LINES = 100

# Escape hatches for ops / dry-runs (documented in template section 7).
NPU_COUNT_OVERRIDE_ENV = "RECIPE_CI_FORCE_NPU_COUNT"
SKIP_WEIGHTS_CHECK_ENV = "RECIPE_CI_SKIP_WEIGHTS_CHECK"


class ComboFailure(Exception):
    """Runtime failure for one combo (marks CI red)."""


class ComboSkip(Exception):
    """Runtime skip for one combo (does not mark CI red)."""


def image_tag(image: str) -> str:
    """Return the image tag (segment after the last ':' when it has no '/')."""
    _head, sep, tag = image.rpartition(":")
    if sep and "/" not in tag:
        return tag
    return ""


def npu_visible_count() -> int | None:
    """Best-effort visible NPU count via npu-smi; None when undeterminable."""
    override = os.environ.get(NPU_COUNT_OVERRIDE_ENV)
    if override:
        return int(override)
    for args, pattern in (
        (["npu-smi", "info", "-l"], r"Total\s+Count\s*:\s*(\d+)"),
        # Fallback: count device table rows like "| 0    910C  ... |".
        (["npu-smi", "info"], r"^\|\s*\d+\s+\S"),
    ):
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        print(f"[precheck] $ {' '.join(args)} -> rc={proc.returncode}\n"
              f"{proc.stdout or proc.stderr}", flush=True)
        if proc.returncode == 0:
            matches = re.findall(pattern, proc.stdout, flags=re.MULTILINE)
            if matches:
                # Table-row pattern: one match per device; "Total Count": one number.
                return len(matches) if pattern.startswith("^") else int(matches[0])
    return None


def weights_cached(model_id: str, hub: str) -> bool:
    """Best-effort weights cache check (template section 7, rule 2)."""
    if hub == "modelscope":
        base = Path(os.environ.get("MODELSCOPE_CACHE") or os.path.expanduser("~/.cache/modelscope"))
        path = base / "hub" / "models" / model_id
        return path.is_dir() and any(path.iterdir())
    base = Path(os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"))
    path = base / "hub" / ("models--" + model_id.replace("/", "--"))
    return path.is_dir() and (path / "snapshots").is_dir() and any((path / "snapshots").iterdir())


def precheck(meta: dict) -> str | None:
    """Return a skip reason, or None when the combo may run.

    NPU visibility is informational only: the runner assignment is trusted,
    so a low npu-smi count is logged but never blocks the run.
    """
    visible = npu_visible_count()
    required = int(meta["required_npu"])
    if visible is not None:
        if visible < required:
            print(f"[precheck] WARNING: {visible} visible NPU(s) < {required} required; "
                  "trusting runner assignment and continuing", flush=True)
        else:
            print(f"[precheck] NPU check ok: {visible} visible >= {required} required", flush=True)

    job_image = os.environ.get("VLLM_ASCEND_IMAGE", "")
    if job_image and image_tag(job_image) != image_tag(str(meta["image"])):
        return f"image mismatch: job={job_image} recipe={meta['image']}"

    if os.environ.get(SKIP_WEIGHTS_CHECK_ENV) != "1":
        models = [m for m in (meta["model_id"], meta.get("draft_model")) if m]
        missing = [m for m in models if not weights_cached(str(m), str(meta["hub"]))]
        if missing:
            return "weights not cached: " + ", ".join(missing)
    return None


def http_get_json(url: str, timeout: int = HTTP_TIMEOUT_S) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def http_post_json(url: str, payload: dict, timeout: int = SMOKE_TIMEOUT_S) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.load(resp)


def start_bg(cmd: list[str], log_path: Path) -> subprocess.Popen:
    """Start a background process in its own session, logging to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log:
        return subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )


def tail_lines(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def wait_ready(proc: subprocess.Popen, base_url: str, timeout_s: int, log_path: Path) -> list[dict]:
    """Poll /v1/models until the engine is ready (template 7, rule 3).

    New serve.log lines are mirrored to stdout so the engine progress streams
    into the job log (mirrors verify-recipe.sh's foreground serve), plus a
    progress line every 30s.
    """
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    last_error: Exception | None = None
    last_pos = 0
    last_progress_at = started
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise ComboFailure(
                f"engine exited early with code {proc.returncode}; last {LOG_TAIL_LINES} lines of {log_path.name}:\n"
                + "\n".join(tail_lines(log_path, LOG_TAIL_LINES))
            )
        # Mirror new serve.log lines into the job log.
        try:
            with open(log_path, "rb") as fh:
                fh.seek(last_pos)
                chunk = fh.read()
                last_pos += len(chunk)
        except OSError:
            chunk = b""
        if chunk:
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                print(f"[serve] {line}", flush=True)
            last_progress_at = time.monotonic()
        elif time.monotonic() - last_progress_at >= 30:
            print(f"[wait_ready] still waiting for {base_url} ... "
                  f"({int(time.monotonic() - started)}s elapsed, timeout {timeout_s}s)", flush=True)
            last_progress_at = time.monotonic()
        try:
            payload = http_get_json(base_url + "/v1/models")
            return list(payload.get("data") or [])
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last_error = e
        time.sleep(POLL_INTERVAL_S)
    raise ComboFailure(
        f"readiness timeout after {timeout_s}s (last error: {last_error}); "
        f"last {LOG_TAIL_LINES} lines of {log_path.name}:\n"
        + "\n".join(tail_lines(log_path, LOG_TAIL_LINES))
    )


def wait_tcp(port: int, timeout_s: int, proc: subprocess.Popen | None = None) -> None:
    """Wait until a companion TCP port accepts connections."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise ComboFailure(f"companion exited early with code {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5):
                return
        except OSError:
            time.sleep(POLL_INTERVAL_S)
    raise ComboFailure(f"companion port {port} not ready after {timeout_s}s")


def ensure_companion(comp: dict, log_path: Path) -> tuple[subprocess.Popen | None, int | None]:
    """Start one companion; install it first when missing (template 6.2)."""
    tokens = shlex.split(str(comp.get("command") or ""))
    if not tokens:
        return None, None
    if not shutil.which(tokens[0]):
        hint = comp.get("install_hint")
        if not hint:
            raise ComboFailure(f"companion {tokens[0]!r} missing and no install hint")
        try:
            subprocess.run(["bash", "-lc", str(hint)], check=True, timeout=600, capture_output=True)
        except (subprocess.SubprocessError, OSError):
            raise ComboSkip(f"companion install failed: {hint}") from None
        if not shutil.which(tokens[0]):
            raise ComboSkip(f"companion {tokens[0]!r} still missing after install")
    return start_bg(["bash", "-lc", str(comp["command"])], log_path), comp.get("port")


def smoke(base_url: str, model: str) -> None:
    """Smoke test: one chat completion with non-empty content (template 7, rule 4)."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say 'ok'."}],
        "max_tokens": SMOKE_MAX_TOKENS,
    }
    try:
        resp = http_post_json(base_url + "/v1/chat/completions", payload)
    except (urllib.error.URLError, OSError) as e:
        raise ComboFailure(f"smoke request failed: {e}") from e
    choices = resp.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not content:
        raise ComboFailure("smoke: empty message content (resp=" + json.dumps(resp)[:400] + ")")


def teardown(procs: list[subprocess.Popen], out_dir: Path) -> None:
    """Terminate process groups, then collect an npu-smi snapshot."""
    for proc in procs:
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + TEARDOWN_WAIT_S
    for proc in procs:
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(1)
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=TEARDOWN_WAIT_S)
    try:
        proc = subprocess.run(["npu-smi", "info"], capture_output=True, text=True, timeout=60)
        (out_dir / "npu-smi.txt").write_text(proc.stdout, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass


def run_combo(combo_dir: Path, results_dir: Path) -> int:
    meta = json.loads((combo_dir / "meta.json").read_text(encoding="utf-8"))
    combo_id = str(meta["combo_id"])
    out_dir = results_dir / combo_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "combo_id": combo_id,
        "hf_id": meta["hf_id"],
        "hardware": meta["hardware"],
        "variant": meta["variant"],
        "features": meta["features_resolved"],
        "image": meta["image"],
        "resolved_strategy": meta["strategy"],
        "deploy_type": meta["deploy_type"],
        "required_npu": meta["required_npu"],
        "port": meta["port"],
        "source_url": meta["source_url"],
        "status": "fail",
        "skip_reason": None,
        "readiness_seconds": None,
        "smoke_seconds": None,
    }

    skip_reason = precheck(meta)
    if skip_reason:
        result.update(status="skip", skip_reason=skip_reason)
    else:
        engine: subprocess.Popen | None = None
        companions: list[subprocess.Popen] = []
        started = time.monotonic()
        try:
            engine = start_bg(["bash", str(combo_dir / "run.sh")], out_dir / "serve.log")
            base_url = f"http://127.0.0.1:{meta['port']}"
            models = wait_ready(engine, base_url, int(meta["readiness_timeout_s"]), out_dir / "serve.log")
            result["readiness_seconds"] = round(time.monotonic() - started, 1)
            for comp in meta.get("companions") or []:
                if not comp.get("after"):
                    continue
                companion, port = ensure_companion(comp, out_dir / "companion.log")
                if companion is not None:
                    companions.append(companion)
                    if port:
                        wait_tcp(int(port), COMPANION_READY_TIMEOUT_S, companion)
            served = str(models[0]["id"]) if models else str(meta["model_id"])
            smoke(base_url, served)
            result["smoke_seconds"] = round(time.monotonic() - started, 1)
            result["status"] = "pass"
        except (ComboFailure, ComboSkip) as e:
            result.update(
                status="skip" if isinstance(e, ComboSkip) else "fail",
                skip_reason=str(e),
            )
        finally:
            teardown([p for p in [engine, *companions] if p is not None], out_dir)

    if result["status"] == "fail":
        result["log_tail"] = tail_lines(out_dir / "serve.log", LOG_TAIL_LINES)
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reason = f": {result['skip_reason']}" if result["skip_reason"] else ""
    print(f"[{combo_id}] {result['status']}{reason}")
    return 0 if result["status"] in ("pass", "skip") else 1


def summarize(results_dir: Path) -> int:
    """Print a markdown table of all result.json files for GITHUB_STEP_SUMMARY."""
    print("| combo | status | reason | readiness (s) | total (s) |")
    print("| --- | --- | --- | --- | --- |")
    for path in sorted(results_dir.glob("*/result.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        print(
            f"| {r.get('combo_id')} | {r.get('status')} | {r.get('skip_reason') or ''} "
            f"| {r.get('readiness_seconds') or ''} | {r.get('smoke_seconds') or ''} |"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combo-dir", type=Path, help="artifact dir of one combo (dist/combo/<id>)")
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--summarize", action="store_true", help="print a summary table and exit")
    args = parser.parse_args(argv)
    if args.summarize:
        return summarize(args.results_dir)
    if not args.combo_dir:
        parser.error("--combo-dir is required unless --summarize is set")
    return run_combo(args.combo_dir, args.results_dir)


if __name__ == "__main__":
    sys.exit(main())
