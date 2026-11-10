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
"""Generate ``vllm serve`` scripts for recipe CI combos.

Implements the generate stage of ``recipes/ci/recipe-ci-template.md``:

1. read a MAP file (``recipes/ci/map.json``),
2. fetch each combo's render from the recipes site (query JSON API),
3. run the content assertions (argv shape, ``selection.adjusted``,
   single-node gate, speculative method, tp/dp/pp expectations),
4. emit per-combo ``run.sh`` + ``meta.json`` + ``raw.json`` plus a workflow
   matrix (``matrix.json``), a manifest and a summary.

Runs on ``ubuntu-latest`` (network required); the NPU stage only consumes the
generated artifacts (see ``recipes/ci/execute.py`` and
``.github/workflows/recipe-e2e.yml``). Stdlib only - no third-party imports.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_SITE = "https://recipes.vllm.ai"
DEFAULT_READINESS_TIMEOUT_S = 1800
# Margin on top of the readiness wait (smoke test + teardown) when deriving
# the GitHub Actions job timeout.
JOB_TIMEOUT_MARGIN_S = 1800
DEFAULT_JOB_TIMEOUT_MIN = 90
DEFAULT_PORT = 8000

NPU_VISIBILITY_ENV = "ASCEND_RT_VISIBLE_DEVICES"
MODELSCOPE_ENV = "VLLM_USE_MODELSCOPE"
EXECUTE_TIMEOUT_ENV = "VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS"
NOT_SINGLE_NODE_STRATEGY_PREFIX = "multi_node_"
PD_STRATEGY = "pd_cluster"

# recipes hardware profile id -> runner chip id (runner_label.json "chip")
HARDWARE_CHIP = {
    "ascend_910c": "a3",
    "ascend_950dt_8x": "a5",
    "ascend_950pr_8x": "a5",
}

_SHELL_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class ComboFailure(Exception):
    """Generate-stage failure for one combo (marks CI red)."""


class ComboSkip(Exception):
    """Generate-stage skip for one combo (does not mark CI red)."""


class FetchError(Exception):
    """HTTP/JSON failure while fetching a render from the recipes site."""


def quote_env_value(value: str) -> str:
    """Shell-quote an env value, turning ``$VAR`` refs into ``${VAR:-}``.

    ``${VAR:-}`` keeps the runtime composition (template 5.1 rule 4) while
    surviving ``set -u`` when the variable is unset.
    """
    parts: list[str] = []
    pos = 0
    for match in _SHELL_VAR_RE.finditer(value):
        if match.start() > pos:
            parts.append(shlex.quote(value[pos : match.start()]))
        name = match.group(1) or match.group(2)
        parts.append("${" + name + ":-}")
        pos = match.end()
    if pos < len(value):
        parts.append(shlex.quote(value[pos:]))
    if not parts:
        return shlex.quote(value)
    return "".join(parts)


def build_combo_url(site: str, hf_id: str, combo: dict) -> str:
    """Build the query JSON API URL for one combo (template 4.1).

    Parameter order and encoding match the site's ``CommandBuilder``: the
    ``fmode`` pairs are ``feature:mode`` joined with commas and the ``:`` is
    percent-encoded (``%3A``).
    """
    params: list[tuple[str, str]] = [
        ("hardware", str(combo["hardware"])),
        ("variant", str(combo["variant"])),
    ]
    features = combo.get("features") or {}
    if features:
        params.append(("features", ",".join(features)))
        params.append(("fmode", ",".join(f"{k}:{v}" for k, v in features.items())))
    params.append(("nodes", "1"))
    query = "&".join(f"{k}={urllib.parse.quote(v, safe=',')}" for k, v in params)
    return f"{site}/{hf_id}.json?{query}"


def fetch_json(url: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise FetchError(url, f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FetchError(url, str(e.reason)) from e
    except json.JSONDecodeError as e:
        raise FetchError(url, f"invalid JSON: {e}") from e


def fetch_render(site: str, hf_id: str, combo: dict) -> tuple[dict, str]:
    """Fetch the combo render; fall back to the variant's promoted endpoint.

    Primary path is the parent endpoint with the variant's own key (template
    4.1). On 404, look up ``variants[<variant>].json`` in the bare parent
    render and retry the query there (the variant key keeps its name).
    Returns ``(render, source_url)``.
    """
    url = build_combo_url(site, hf_id, combo)
    try:
        return fetch_json(url), url
    except FetchError as first_error:
        if "HTTP 404" not in str(first_error):
            raise
        bare = fetch_json(f"{site}/{hf_id}.json")
        variants = bare.get("variants") or {}
        pointer = str(variants.get(combo["variant"], {}).get("json") or "").lstrip("/")
        if not pointer:
            raise first_error
        promoted_id = pointer.removesuffix(".json")
        url = build_combo_url(site, promoted_id, combo)
        return fetch_json(url), url


def argv_value(argv: list[str], flag: str) -> str | None:
    for i, token in enumerate(argv):
        if token == flag and i + 1 < len(argv):
            return argv[i + 1]
    return None


def argv_int(argv: list[str], flag: str) -> int | None:
    value = argv_value(argv, flag)
    return int(value) if value is not None else None


def parse_spec_config(argv: list[str]) -> dict:
    raw = argv_value(argv, "--speculative-config")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ComboFailure(f"--speculative-config is not valid JSON: {e}") from e
    return parsed if isinstance(parsed, dict) else {}


def assert_not_adjusted(render: dict) -> None:
    """4.2 assertion 4: the server must not have silently adjusted params.

    The site records hardware/variant rewrites in ``selection.adjusted``
    (e.g. ``ascend_910c`` -> ``h200``); ``selection.hardware`` then echoes the
    *adjusted* value, so only this array exposes the fallback.
    """
    adjusted = (render.get("selection") or {}).get("adjusted") or []
    if adjusted:
        detail = "; ".join(
            f"{a.get('param')} {a.get('requested')!r}->{a.get('used')!r} ({a.get('reason')})" for a in adjusted
        )
        raise ComboFailure(f"command fallback mismatch: server adjusted params: {detail}")


def assert_single_node(render: dict) -> None:
    """4.2 assertion 3: single-node boundary (the multi-node/PD gate)."""
    strategy = str(render.get("strategy") or "")
    reasons = []
    if render.get("deploy_type") != "single_node":
        reasons.append(f"deploy_type={render.get('deploy_type')!r}")
    if render.get("node_count", 1) != 1:
        reasons.append(f"node_count={render.get('node_count')!r}")
    if strategy.startswith(NOT_SINGLE_NODE_STRATEGY_PREFIX) or strategy == PD_STRATEGY:
        reasons.append(f"strategy={strategy!r}")
    if (render.get("selection") or {}).get("pd"):
        reasons.append("selection.pd set")
    if reasons:
        raise ComboSkip("multi_node/pd not supported yet: " + ", ".join(reasons))


def resolve_spec_mode(combo: dict, argv: list[str]) -> dict:
    """4.2 assertion 1: speculative method vs expectation.

    - explicit ``features.spec_decoding`` request -> method must equal the
      requested mode (or the ``expect`` override, which stores the *method*
      value - mode id and method may differ, e.g. mode ``eagle3_gqa`` renders
      ``method: eagle3``);
    - empty ``features`` -> record the actual method only, unless ``expect``
      upgrades it to a strong assertion (variants inject default modes, so
      `no features` never means `no feature args`).

    Returns the resolved features dict for ``result.json``.
    """
    spec = parse_spec_config(argv)
    method = spec.get("method")
    requested = (combo.get("features") or {}).get("spec_decoding")
    expected = (combo.get("expect") or {}).get("spec_decoding", requested)
    if expected is not None and method != expected:
        raise ComboFailure(f"command fallback mismatch: speculative method {method!r} != expected {expected!r}")
    return {"spec_decoding": method} if method else {}


def assert_parallel_sizes(combo: dict, argv: list[str]) -> tuple[int, int, int]:
    """4.2 assertion 2: tp/dp/pp vs ``expect`` (record-only when unset)."""
    tp = argv_int(argv, "--tensor-parallel-size") or 1
    dp = argv_int(argv, "--data-parallel-size") or 1
    pp = argv_int(argv, "--pipeline-parallel-size") or 1
    for key, actual in (("tp", tp), ("dp", dp), ("pp", pp)):
        want = (combo.get("expect") or {}).get(key)
        if want is not None and actual != int(want):
            raise ComboFailure(f"command fallback mismatch: {key}={actual} != expected {want}")
    return tp, dp, pp


def parse_companions(render: dict) -> list[dict]:
    """Extract companion processes (template 6.2): command, port, after flag
    and the install hint from the description's ``Requires:`` line."""
    companions = []
    for comp in render.get("companions") or []:
        command = str(comp.get("command") or "")
        tokens = shlex.split(command)
        port = None
        for i, token in enumerate(tokens):
            if token == "--port" and i + 1 < len(tokens):
                try:
                    port = int(tokens[i + 1])
                except ValueError:
                    port = None
        match = re.search(r"Requires:\s*(.+)", str(comp.get("description") or ""))
        companions.append(
            {
                "label": comp.get("label"),
                "command": command,
                "port": port,
                "after": bool(comp.get("after")),
                "install_hint": match.group(1).strip() if match else None,
            }
        )
    return companions


def derive_timeouts(env: dict) -> tuple[int, int]:
    """Readiness timeout and GHA job timeout (template 7).

    ``VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS`` (e.g. 7200 on 1M-context combos)
    widens both so long first loads are not killed by the job limit.
    """
    readiness = DEFAULT_READINESS_TIMEOUT_S
    try:
        execute_timeout = int(env.get(EXECUTE_TIMEOUT_ENV, "0"))
    except (TypeError, ValueError):
        execute_timeout = 0
    readiness = max(readiness, execute_timeout)
    job_minutes = max(DEFAULT_JOB_TIMEOUT_MIN, math.ceil((readiness + JOB_TIMEOUT_MARGIN_S) / 60))
    return readiness, job_minutes


def rewrite_image(image: str, rewrites: list[dict]) -> str:
    """Apply the MAP's registry rewrites (quay.io -> SWR mirror) so NPU
    runners can pull the recipe image (template risk 9)."""
    for rule in rewrites:
        src = str(rule.get("from") or "")
        if src and image.startswith(src):
            return image.replace(src, str(rule.get("to") or ""), 1)
    return image


def pick_runner(runner_labels: dict, hardware: str, required_npu: int, override: str | None) -> str:
    """Pick a runner label from runner_label.json: same chip, smallest card
    count that fits (template section 9). Raises ComboSkip when none fits."""
    if override:
        return str(override)
    chip = HARDWARE_CHIP.get(hardware)
    if chip is None:
        raise ComboFailure(f"unknown hardware profile {hardware!r}; add it to HARDWARE_CHIP in generate.py")
    candidates = sorted(
        (info["npu_num"], label)
        for label, info in runner_labels.items()
        if info.get("chip") == chip and info.get("npu_num", 0) >= required_npu
    )
    if not candidates:
        raise ComboSkip(f"insufficient NPU: no '{chip}' runner with >= {required_npu} cards")
    return candidates[0][1]


def plan_combo(
    entry: dict,
    combo: dict,
    render: dict,
    source_url: str,
    runner_labels: dict,
    rewrites: list[dict],
) -> dict:
    """Validate one render and derive the execution plan (template 5/6).

    Raises ComboFailure (fallback mismatch / bad shape) or ComboSkip
    (multi-node, no runner). Pure function: no I/O.
    """
    argv = list(render.get("argv") or [])
    if argv[:2] != ["vllm", "serve"]:
        raise ComboFailure(f"argv must start with 'vllm serve', got {argv[:2]!r}")

    assert_not_adjusted(render)
    assert_single_node(render)
    features_resolved = resolve_spec_mode(combo, argv)
    tp, dp, pp = assert_parallel_sizes(combo, argv)

    env = dict(render.get("env") or {})
    visible = env.get(NPU_VISIBILITY_ENV)
    visible_count = len(str(visible).split(",")) if visible else 0
    required_npu = max(tp * dp * pp, visible_count)

    image = str(render.get("docker_image") or "")
    if not image:
        raise ComboFailure("render has no docker_image")

    hub = "modelscope" if str(env.get(MODELSCOPE_ENV, "")).lower() in ("true", "1", "yes") else "huggingface"
    readiness, job_minutes = derive_timeouts(env)
    runner = pick_runner(runner_labels, combo["hardware"], required_npu, combo.get("runner"))
    runner_image = str(combo.get("runner_image") or rewrite_image(image, rewrites))

    return {
        "combo_id": combo["id"],
        "entry_name": entry.get("name"),
        "hf_id": entry["hf_id"],
        "yaml": entry.get("yaml"),
        "hardware": combo["hardware"],
        "variant": combo["variant"],
        "features_requested": combo.get("features") or {},
        "features_resolved": features_resolved,
        "image": image,
        "runner_image": runner_image,
        "runner": runner,
        "npu_list": ",".join(str(i) for i in range(required_npu)),
        "required_npu": required_npu,
        "tp": tp,
        "dp": dp,
        "pp": pp,
        "port": argv_int(argv, "--port") or DEFAULT_PORT,
        "model_id": argv[2],
        "draft_model": parse_spec_config(argv).get("model"),
        "hub": hub,
        "env": env,
        "argv": argv,
        "companions": parse_companions(render),
        "strategy": render.get("strategy"),
        "deploy_type": render.get("deploy_type"),
        "source_url": source_url,
        "readiness_timeout_s": readiness,
        "job_timeout_min": job_minutes,
    }


def render_run_sh(meta: dict) -> str:
    """Render ``run.sh`` from the plan (template 5.1 / 7).

    - env dict -> ``export K=V`` (``$VAR`` values become ``${VAR:-}``),
    - ``ASCEND_RT_VISIBLE_DEVICES`` is dropped from the recipe env and set
      from ``CI_NPU_LIST`` instead (override-if-present / add-if-missing),
    - the argv is re-assembled with per-token shell quoting.
    """
    lines = [
        "#!/usr/bin/env bash",
        f"# Generated by recipes/ci/generate.py for combo {meta['combo_id']}",
        f"# source: {meta['source_url']}",
        "# Do not edit by hand; CI overrides are at the bottom.",
        "set -euo pipefail",
        "",
        "# --- env from the recipe render ---",
    ]
    for key in sorted(meta["env"]):
        if key == NPU_VISIBILITY_ENV:
            continue
        lines.append(f"export {key}={quote_env_value(str(meta['env'][key]))}")
    lines += [
        "",
        "# --- CI overrides ---",
        f'export {NPU_VISIBILITY_ENV}="${{CI_NPU_LIST:?CI_NPU_LIST must be set by the CI job}}"',
        "",
        "exec " + " ".join(shlex.quote(token) for token in meta["argv"]),
        "",
    ]
    return "\n".join(lines)


def process_combo(
    entry: dict,
    combo: dict,
    site: str,
    runner_labels: dict,
    rewrites: list[dict],
    out_dir: Path,
) -> dict:
    """Fetch + plan + write artifacts for one combo. Never raises."""
    row = {
        "combo_id": combo["id"],
        "hf_id": entry["hf_id"],
        "status": "failed",
        "reason": None,
        "runner": None,
        "image": None,
        "npu_list": None,
        "timeout_minutes": None,
    }
    try:
        if (combo.get("nodes") or 1) != 1:
            raise ComboSkip("multi_node not supported yet (nodes > 1)")
        try:
            render, source_url = fetch_render(site, str(entry["hf_id"]), combo)
        except FetchError as e:
            raise ComboFailure(f"fetch failed: {e}") from e
        meta = plan_combo(entry, combo, render, source_url, runner_labels, rewrites)
        combo_dir = out_dir / "combo" / str(combo["id"])
        combo_dir.mkdir(parents=True, exist_ok=True)
        (combo_dir / "run.sh").write_text(render_run_sh(meta), encoding="utf-8")
        (combo_dir / "run.sh").chmod(0o755)
        (combo_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (combo_dir / "raw.json").write_text(json.dumps(render, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        row.update(
            status="generated",
            runner=meta["runner"],
            image=meta["runner_image"],
            npu_list=meta["npu_list"],
            timeout_minutes=meta["job_timeout_min"],
        )
    except ComboSkip as e:
        row.update(status="skipped", reason=str(e))
    except ComboFailure as e:
        row.update(status="failed", reason=str(e))
    return row


def write_outputs(out_dir: Path, rows: list[dict]) -> int:
    """Write manifest.json / matrix.json / summary.md; return exit code."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps({"combos": rows}, indent=2) + "\n", encoding="utf-8")
    include = [
        {
            "combo": r["combo_id"],
            "runner": r["runner"],
            "image": r["image"],
            "npu_list": r["npu_list"],
            "timeout_minutes": r["timeout_minutes"],
        }
        for r in rows
        if r["status"] == "generated"
    ]
    (out_dir / "matrix.json").write_text(json.dumps({"include": include}, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Recipe CI generate summary",
        "",
        "| combo | status | reason | runner | image |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['combo_id']} | {r['status']} | {r['reason'] or ''} | {r['runner'] or ''} | {r['image'] or ''} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if any(r["status"] == "failed" for r in rows) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, type=Path, help="MAP json (recipes/ci/map.json)")
    parser.add_argument("--out", required=True, type=Path, help="output directory for artifacts")
    parser.add_argument(
        "--runner-labels",
        required=True,
        type=Path,
        help="runner inventory (.github/workflows/scripts/runner_label.json)",
    )
    args = parser.parse_args(argv)

    map_data = json.loads(args.map.read_text(encoding="utf-8"))
    if map_data.get("schema_version") != SCHEMA_VERSION:
        print(f"unsupported MAP schema_version: {map_data.get('schema_version')!r}", file=sys.stderr)
        return 2
    runner_labels = json.loads(args.runner_labels.read_text(encoding="utf-8"))
    site = str(map_data.get("site") or DEFAULT_SITE)
    rewrites = list(map_data.get("runner_image_rewrites") or [])

    rows = []
    for entry in map_data.get("entries", []):
        for combo in entry.get("combos", []):
            row = process_combo(entry, combo, site, runner_labels, rewrites, args.out)
            rows.append(row)
            print(f"[{row['combo_id']}] {row['status']}" + (f": {row['reason']}" if row["reason"] else ""))

    exit_code = write_outputs(args.out, rows)
    print(
        f"wrote artifacts to {args.out} ({len(rows)} combos, "
        f"{sum(r['status'] == 'generated' for r in rows)} generated, "
        f"{sum(r['status'] == 'skipped' for r in rows)} skipped, "
        f"{sum(r['status'] == 'failed' for r in rows)} failed)"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
