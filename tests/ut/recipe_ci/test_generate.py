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
"""Unit tests for the recipe CI generator (recipes/ci/generate.py).

The fixtures mirror real renders fetched from recipes.vllm.ai and its PR
preview site (see recipes/ci/recipe-ci-template.md sections 4 and 5). Only
the pure planning logic is tested here; network fetches are exercised by the
CI workflow itself.
"""

import importlib.util
import json
import shlex
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GENERATE_PATH = _REPO_ROOT / "recipes" / "ci" / "generate.py"

_spec = importlib.util.spec_from_file_location("recipe_ci_generate", _GENERATE_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

RUNNER_LABELS = {
    "linux-aarch64-a3-2": {"chip": "a3", "npu_num": 2},
    "linux-aarch64-a3-4": {"chip": "a3", "npu_num": 4},
    "linux-aarch64-a3-800t-16": {"chip": "a3", "npu_num": 16},
    "linux-aarch64-a5-4": {"chip": "a5", "npu_num": 4},
}

REWRITES = [{"from": "quay.io/ascend/", "to": "swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/"}]

# Shape of the DeepSeek-V4-Flash A5 default render (live site, 2026-11).
A5_RENDER = {
    "argv": [
        "vllm",
        "serve",
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        "--trust-remote-code",
        "--port",
        "8100",
        "--data-parallel-size",
        "4",
        "--speculative-config",
        '{"method":"dspark","num_speculative_tokens":5,"enforce_eager":true}',
    ],
    "env": {
        "PYTORCH_NPU_ALLOC_CONF": "expandable_segments:True",
        "HCCL_BUFFSIZE": "1024",
        "ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3",
        "VLLM_USE_RUST_FRONTEND": "1",
    },
    "docker_image": "quay.io/ascend/vllm-ascend:v0.23.0-a5",
    "strategy": "single_node_dep",
    "deploy_type": "single_node",
    "node_count": 1,
    "selection": {"hardware": "ascend_950dt_8x", "adjusted": [], "pd": None},
    "companions": [
        {
            "label": "vLLM Router",
            "after": True,
            "description": "Routes client requests. Requires: uv pip install vllm-router",
            "command": (
                "vllm-router \\\n  --host 0.0.0.0 \\\n  --port 8000 \\\n"
                "  --intra-node-data-parallel-size 4 \\\n  --worker-urls http://localhost:8100"
            ),
        }
    ],
}

A5_ENTRY = {
    "name": "DeepSeek-V4-Flash",
    "yaml": "recipes/overlays/deepseek-ai/DeepSeek-V4-Flash.yaml",
    "hf_id": "deepseek-ai/DeepSeek-V4-Flash",
}

A5_COMBO = {
    "id": "a5-950dt-default",
    "hardware": "ascend_950dt_8x",
    "variant": "default",
    "features": {},
    "expect": {"spec_decoding": "dspark", "dp": 4},
    "nodes": 1,
}

SOURCE_URL = (
    "https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash.json?hardware=ascend_950dt_8x&variant=default&nodes=1"
)


def _plan(render: dict, combo: dict | None = None, entry: dict | None = None) -> dict:
    return gen.plan_combo(
        entry or A5_ENTRY,
        combo or A5_COMBO,
        render,
        SOURCE_URL,
        RUNNER_LABELS,
        REWRITES,
    )


# --- URL building (template 4.1) -------------------------------------------


def test_build_combo_url_without_features():
    url = gen.build_combo_url("https://recipes.vllm.ai", "deepseek-ai/DeepSeek-V4-Flash", A5_COMBO)
    assert url == (
        "https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash.json?hardware=ascend_950dt_8x&variant=default&nodes=1"
    )


def test_build_combo_url_encodes_fmode():
    combo = {"hardware": "ascend_950dt_8x", "variant": "default", "features": {"spec_decoding": "dflash"}}
    url = gen.build_combo_url("https://s", "org/model", combo)
    assert "features=spec_decoding&" in url
    assert "fmode=spec_decoding%3Adflash&" in url
    assert url.endswith("&nodes=1")


# --- env value quoting (template 5.1 rule 4) --------------------------------


def test_quote_env_value_plain():
    assert gen.quote_env_value("expandable_segments:True") == "expandable_segments:True"


def test_quote_env_value_with_shell_var():
    value = "/usr/lib/aarch64-linux-gnu/libjemalloc.so.2:$LD_PRELOAD"
    quoted = gen.quote_env_value(value)
    assert quoted == "/usr/lib/aarch64-linux-gnu/libjemalloc.so.2:${LD_PRELOAD:-}"
    # The rendered assignment must not reference an unguarded $LD_PRELOAD.
    assert "$LD_PRELOAD" not in quoted.replace("${LD_PRELOAD:-}", "")


def test_quote_env_value_with_braced_var():
    quoted = gen.quote_env_value("a${FOO}b")
    assert quoted == "a${FOO:-}b"


# --- happy path planning (template 5 / 6) -----------------------------------


def test_plan_combo_happy_path():
    meta = _plan(A5_RENDER)
    assert meta["model_id"] == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert meta["required_npu"] == 4  # dp=4 and visible devices=0,1,2,3
    assert meta["tp"] == 1 and meta["dp"] == 4 and meta["pp"] == 1
    assert meta["port"] == 8100
    assert meta["hub"] == "huggingface"
    assert meta["draft_model"] is None
    assert meta["features_resolved"] == {"spec_decoding": "dspark"}
    assert meta["runner"] == "linux-aarch64-a5-4"
    assert meta["image"] == "quay.io/ascend/vllm-ascend:v0.23.0-a5"
    assert meta["runner_image"] == ("swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:v0.23.0-a5")
    assert meta["readiness_timeout_s"] == 1800
    assert meta["job_timeout_min"] == 90
    # Companion: vllm-router on port 8000 with an install hint.
    assert len(meta["companions"]) == 1
    companion = meta["companions"][0]
    assert companion["port"] == 8000
    assert companion["after"] is True
    assert companion["install_hint"] == "uv pip install vllm-router"


def test_plan_combo_modelscope_and_draft_model():
    # Shape of a W8A8 + eagle3 render (PR21065 MiniMax-M3 style).
    render = {
        "argv": [
            "vllm",
            "serve",
            "Eco-Tech/MiniMax-M3-w8a8-0626",
            "--tensor-parallel-size",
            "4",
            "--data-parallel-size",
            "4",
            "--quantization",
            "ascend",
            "--speculative-config",
            '{"model":"Inferact/MiniMax-M3-EAGLE3-GQA","method":"eagle3","num_speculative_tokens":3}',
        ],
        "env": {"VLLM_USE_MODELSCOPE": "True", "VLLM_PP_LAYER_PARTITION": "15,15,15,15"},
        "docker_image": "quay.io/ascend/vllm-ascend:v0.30.0-a3",
        "strategy": "single_node_tp",
        "deploy_type": "single_node",
        "node_count": 1,
        "selection": {"adjusted": [], "pd": None},
    }
    combo = {
        "id": "a3-910c-w8a8",
        "hardware": "ascend_910c",
        "variant": "ascend_w8a8",
        "features": {},
        # eagle3_gqa mode renders method "eagle3": expect stores the method.
        "expect": {"spec_decoding": "eagle3"},
    }
    meta = _plan(render, combo)
    assert meta["hub"] == "modelscope"
    assert meta["draft_model"] == "Inferact/MiniMax-M3-EAGLE3-GQA"
    # No ASCEND_RT_VISIBLE_DEVICES in env: required_npu = tp*dp = 16.
    assert meta["required_npu"] == 16
    assert meta["runner"] == "linux-aarch64-a3-800t-16"
    assert meta["port"] == 8000  # no --port in argv


def test_plan_combo_max_with_visible_devices():
    # Visible devices (16) exceed tp*dp (4): the runner must fit the wider list.
    # Uses an A3 combo because runner_label.json only has a 16-card A3 runner.
    render = dict(A5_RENDER)
    render["env"] = dict(A5_RENDER["env"], ASCEND_RT_VISIBLE_DEVICES=",".join(map(str, range(16))))
    meta = _plan(render, dict(A5_COMBO, hardware="ascend_910c"))
    assert meta["required_npu"] == 16  # max(dp=4, visible=16)
    assert meta["runner"] == "linux-aarch64-a3-800t-16"


# --- assertion failures (template 4.2) --------------------------------------


def test_plan_combo_rejects_non_vllm_serve_argv():
    render = dict(A5_RENDER, argv=["python", "-m", "vllm.entrypoints.api_server", "model"])
    with pytest.raises(gen.ComboFailure, match="vllm serve"):
        _plan(render)


def test_plan_combo_rejects_adjusted_params():
    render = dict(A5_RENDER)
    render["selection"] = {
        "adjusted": [
            {
                "param": "hardware",
                "requested": "ascend_910c",
                "used": "h200",
                "reason": "not available for variant `default`",
            }
        ]
    }
    with pytest.raises(gen.ComboFailure, match="adjusted params.*ascend_910c.*h200"):
        _plan(render)


@pytest.mark.parametrize(
    "patch",
    [
        {"strategy": "multi_node_tp_dp", "deploy_type": "multi_node", "node_count": 2},
        {"strategy": "pd_cluster", "deploy_type": "pd"},
    ],
)
def test_plan_combo_skips_non_single_node(patch):
    render = dict(A5_RENDER)
    render.update(patch)
    render["selection"] = {"adjusted": [], "pd": None}
    with pytest.raises(gen.ComboSkip, match="multi_node/pd not supported yet"):
        _plan(render)


def test_plan_combo_spec_method_mismatch_fails():
    # A5 default + requested mtp falls back to dspark on the site (4.2).
    combo = dict(A5_COMBO, features={"spec_decoding": "mtp"}, expect={"dp": 4})
    with pytest.raises(gen.ComboFailure, match="speculative method 'dspark' != expected 'mtp'"):
        _plan(A5_RENDER, combo)


def test_plan_combo_spec_record_only_without_expect():
    combo = dict(A5_COMBO, expect={"dp": 4})  # no expect.spec_decoding
    meta = _plan(A5_RENDER, combo)
    assert meta["features_resolved"] == {"spec_decoding": "dspark"}


def test_plan_combo_dp_expectation_mismatch_fails():
    combo = dict(A5_COMBO, expect={"spec_decoding": "dspark", "dp": 8})
    with pytest.raises(gen.ComboFailure, match="dp=4 != expected 8"):
        _plan(A5_RENDER, combo)


def test_plan_combo_skips_when_no_runner_fits():
    combo = dict(A5_COMBO, hardware="ascend_950dt_8x", expect={"spec_decoding": "dspark", "dp": 8})
    render = dict(A5_RENDER)
    render["argv"] = list(A5_RENDER["argv"])
    idx = render["argv"].index("--data-parallel-size")
    render["argv"][idx + 1] = "8"
    with pytest.raises(gen.ComboSkip, match="insufficient NPU"):
        _plan(render, combo)


# --- timeouts (template 7) ---------------------------------------------------


def test_derive_timeouts_execute_timeout_env_widens_limits():
    env = {"VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS": "7200"}
    readiness, job_minutes = gen.derive_timeouts(env)
    assert readiness == 7200
    assert job_minutes == 150  # ceil((7200 + 1800) / 60)


# --- run.sh rendering (template 5.1 / 7) -------------------------------------


def test_render_run_sh_keeps_recipe_visibility_and_quotes_argv():
    meta = _plan(A5_RENDER)
    meta["env"]["LD_PRELOAD"] = "/usr/lib/aarch64-linux-gnu/libjemalloc.so.2:$LD_PRELOAD"
    script = gen.render_run_sh(meta)
    lines = script.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in lines
    # Recipe env exports...
    assert "export HCCL_BUFFSIZE=1024" in lines
    assert "export VLLM_USE_RUST_FRONTEND=1" in lines
    assert "export LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libjemalloc.so.2:${LD_PRELOAD:-}" in lines
    # ...and ASCEND_RT_VISIBLE_DEVICES ships exactly as the recipe render
    # provides it; no synthetic CI_NPU_LIST override (runner scheduling trusted).
    assert "export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3" in lines
    assert "CI_NPU_LIST" not in script
    # argv is quoted per token, including the spec-config JSON blob.
    exec_lines = [ln for ln in lines if ln.startswith("exec ")]
    assert len(exec_lines) == 1
    assert "--speculative-config" in exec_lines[0]
    spec_config = '{"method":"dspark","num_speculative_tokens":5,"enforce_eager":true}'
    assert shlex.quote(spec_config) in exec_lines[0]


# --- image rewrite / runner picking ------------------------------------------


def test_rewrite_image_no_match_passthrough():
    assert gen.rewrite_image("vllm/vllm-openai:v0.28.0", REWRITES) == "vllm/vllm-openai:v0.28.0"


def test_pick_runner_prefers_smallest_fitting():
    assert gen.pick_runner(RUNNER_LABELS, "ascend_910c", 4, None) == "linux-aarch64-a3-4"
    assert gen.pick_runner(RUNNER_LABELS, "ascend_910c", 16, None) == "linux-aarch64-a3-800t-16"
    assert gen.pick_runner(RUNNER_LABELS, "ascend_910c", 4, "linux-aarch64-a3-800t-16") == "linux-aarch64-a3-800t-16"


def test_pick_runner_unknown_hardware_fails():
    with pytest.raises(gen.ComboFailure, match="unknown hardware"):
        gen.pick_runner(RUNNER_LABELS, "ascend_960c", 4, None)


# --- --hardware chip filter (manual dispatch) ---------------------------------


def test_main_hardware_filter(tmp_path, monkeypatch):
    # map with one A5 combo and one A3 combo; --hardware a5 must plan only the A5 one.
    map_file = tmp_path / "map.json"
    map_file.write_text(
        json.dumps(
            {
                "schema_version": gen.SCHEMA_VERSION,
                "entries": [
                    {
                        "name": "M",
                        "yaml": "recipes/overlays/org/M.yaml",
                        "hf_id": "org/M",
                        "combos": [
                            {"id": "a5-c", "hardware": "ascend_950dt_8x", "variant": "default", "nodes": 1},
                            {"id": "a3-c", "hardware": "ascend_910c", "variant": "default", "nodes": 1},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    labels_file = tmp_path / "runner_label.json"
    labels_file.write_text(
        json.dumps({"linux-aarch64-a5-4": {"chip": "a5", "npu_num": 4}, "linux-aarch64-a3-4": {"chip": "a3", "npu_num": 4}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gen, "fetch_render", lambda *a, **k: (dict(A5_RENDER), "https://s/x.json"))

    out_dir = tmp_path / "out"
    rc = gen.main(["--map", str(map_file), "--out", str(out_dir), "--runner-labels", str(labels_file), "--hardware", "a5"])
    assert rc == 0
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [r["combo_id"] for r in manifest["combos"]] == ["a5-c"]
    matrix = json.loads((out_dir / "matrix.json").read_text(encoding="utf-8"))
    assert [m["combo"] for m in matrix["include"]] == ["a5-c"]
