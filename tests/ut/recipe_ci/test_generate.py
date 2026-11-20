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

# Shape of the DeepSeek-V4-Flash A3 W8A8+mtp render (live site, 2026-11):
# the strategy layer only exposes DP+EP, so the site ignores a tp query param
# and renders dp=4 with ASCEND_RT_VISIBLE_DEVICES=0,1,2,3.
A3_RENDER = {
    "argv": [
        "vllm",
        "serve",
        "Eco-Tech/DeepSeek-V4-Flash-w8a8-mtp",
        "--trust-remote-code",
        "--quantization",
        "ascend",
        "--enable-expert-parallel",
        "--port",
        "8100",
        "--data-parallel-size",
        "4",
        "--max-num-seqs",
        "64",
        "--speculative-config",
        '{"method":"mtp","num_speculative_tokens":1,"enforce_eager":true}',
    ],
    "env": {
        "ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3",
        "HCCL_BUFFSIZE": "1024",
        "VLLM_USE_MODELSCOPE": "True",
    },
    "docker_image": "quay.io/ascend/vllm-ascend:v0.23.0-a3",
    "strategy": "single_node_dep",
    "deploy_type": "single_node",
    "node_count": 1,
    "selection": {"hardware": "ascend_910c", "adjusted": [], "pd": None},
}


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


def test_build_combo_url_passes_frontend():
    # frontend=python drops VLLM_USE_RUST_FRONTEND from the rendered env;
    # combos opt in via the map so images without the vllm-rs binary can serve.
    combo = {"hardware": "ascend_910c", "variant": "ascend_w8a8", "frontend": "python"}
    url = gen.build_combo_url("https://s", "org/model", combo)
    assert "frontend=python&" in url
    combo.pop("frontend")
    assert "frontend=" not in gen.build_combo_url("https://s", "org/model", combo)


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
    assert meta["bench"] is True  # chat combos benchmark by default
    assert meta["features_resolved"] == {"spec_decoding": "dspark"}
    assert meta["runner"] == "linux-aarch64-a5-4"
    assert meta["image"] == "quay.io/ascend/vllm-ascend:v0.23.0-a5"
    assert meta["runner_image"] == ("swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:v0.23.0-a5")
    assert meta["readiness_timeout_s"] == 1800
    assert meta["job_timeout_min"] == 90


def test_plan_combo_bench_follows_smoke_test_and_override():
    # Rerank combos have no page "Bench" snippet, so benchmarking stays off
    # unless the map turns it on; chat combos can opt out explicitly.
    assert _plan(A5_RENDER, dict(A5_COMBO, smoke_test="rerank"))["bench"] is False
    assert _plan(A5_RENDER, dict(A5_COMBO, smoke_test="rerank", bench=True))["bench"] is True
    assert _plan(A5_RENDER, dict(A5_COMBO, bench=False))["bench"] is False


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


def test_plan_combo_uses_declared_device_mounts():
    # A3 card carries two dies: the recipe declares 16 /dev/davinci mounts (8
    # cards) while argv/env only say 4. The wider declaration must win, and the
    # runner pools are sized in the same units (verification_plan.py parity).
    render = dict(A5_RENDER)
    render["docker_argv"] = ["docker", "run"] + [
        token for i in range(16) for token in ("--device", f"/dev/davinci{i}")
    ] + ["--device", "/dev/davinci_manager", "--device", "/dev/hisi_hdc"]
    meta = _plan(render, dict(A5_COMBO, hardware="ascend_910c"))
    assert meta["required_npu"] == 16  # from the 16 declared device mounts
    assert meta["runner"] == "linux-aarch64-a3-800t-16"


def test_declared_device_count_falls_back_to_command():
    # No docker_argv: count the mounts in the docker command string instead.
    render = {
        "docker_command": "docker run --device /dev/davinci0 --device /dev/davinci1 "
        "--device=/dev/davinci2 --device /dev/davinci_manager",
    }
    assert gen.declared_device_count(render) == 3
    assert gen.declared_device_count({}) == 0


# --- combo override (map-side command rewrite) -------------------------------


def test_override_flag_replaces_dedupes_and_appends():
    # Replaces the value in place...
    assert gen.override_flag(["--data-parallel-size", "4"], "--data-parallel-size", "8") == [
        "--data-parallel-size",
        "8",
    ]
    # ...drops later duplicates...
    assert gen.override_flag(["--a", "1", "--b", "2", "--a", "3"], "--a", "9") == ["--a", "9", "--b", "2"]
    # ...and appends when the flag is absent.
    assert gen.override_flag(["vllm", "serve"], "--tensor-parallel-size", "4") == [
        "vllm",
        "serve",
        "--tensor-parallel-size",
        "4",
    ]


def test_apply_override_last_wins_on_args_and_env():
    argv, env = gen.apply_override(
        {
            "override": {
                "extra_args": {"--data-parallel-size": "8"},
                "extra_env": {"ASCEND_RT_VISIBLE_DEVICES": "0,1"},
            }
        },
        ["vllm", "serve", "m", "--data-parallel-size", "4"],
        {"ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3", "HCCL_BUFFSIZE": "1024"},
    )
    assert argv.count("--data-parallel-size") == 1
    assert gen.argv_int(argv, "--data-parallel-size") == 8
    assert env["ASCEND_RT_VISIBLE_DEVICES"] == "0,1"
    assert env["HCCL_BUFFSIZE"] == "1024"  # untouched env keys survive


def test_apply_override_without_override_is_a_noop():
    argv = ["vllm", "serve", "m"]
    env = {"HCCL_BUFFSIZE": "1024"}
    assert gen.apply_override({}, argv, env) == (argv, env)


def test_plan_combo_applies_override():
    # The site renders A3 as DP+EP (tp params are ignored); the map injects
    # tp=4 and widens the device list so the 16-die runner is used (dp4*tp4=16).
    combo = {
        "id": "a3-910c-w8a8-mtp",
        "hardware": "ascend_910c",
        "variant": "ascend_w8a8",
        "features": {"spec_decoding": "mtp"},
        "expect": {"dp": 4, "tp": 4},
        "override": {
            "extra_args": {"--tensor-parallel-size": "4"},
            "extra_env": {"ASCEND_RT_VISIBLE_DEVICES": ",".join(map(str, range(16)))},
        },
    }
    meta = _plan(A3_RENDER, combo)
    assert meta["tp"] == 4 and meta["dp"] == 4
    assert meta["required_npu"] == 16
    assert meta["runner"] == "linux-aarch64-a3-800t-16"
    assert meta["override"] == combo["override"]
    assert meta["argv"].count("--tensor-parallel-size") == 1
    # run.sh carries the overridden command and env.
    script = gen.render_run_sh(meta)
    assert "--tensor-parallel-size 4" in script
    assert f"export ASCEND_RT_VISIBLE_DEVICES={','.join(map(str, range(16)))}" in script


def test_plan_combo_override_drives_expectation_mismatch():
    # With no override the site render stays dp=4/tp=1, so expect.tp=4 fails:
    # the map must actually inject tp, expecting it alone is not enough.
    combo = dict(
        id="a3-910c-w8a8-mtp",
        hardware="ascend_910c",
        variant="ascend_w8a8",
        features={"spec_decoding": "mtp"},
        expect={"dp": 4, "tp": 4},
    )
    with pytest.raises(gen.ComboFailure, match="tp=1 != expected 4"):
        _plan(A3_RENDER, combo)


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

    # Multiple chips (with whitespace tolerance) keep every matching combo.
    out_dir2 = tmp_path / "out-multi"
    rc = gen.main(["--map", str(map_file), "--out", str(out_dir2), "--runner-labels", str(labels_file), "--hardware", " a3, a5 "])
    assert rc == 0
    manifest = json.loads((out_dir2 / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(r["combo_id"] for r in manifest["combos"]) == ["a3-c", "a5-c"]
    matrix = json.loads((out_dir2 / "matrix.json").read_text(encoding="utf-8"))
    assert sorted(m["combo"] for m in matrix["include"]) == ["a3-c", "a5-c"]


# --- --models filter (manual dispatch) ----------------------------------------


def _two_model_map(tmp_path):
    map_file = tmp_path / "map.json"
    map_file.write_text(
        json.dumps(
            {
                "schema_version": gen.SCHEMA_VERSION,
                "entries": [
                    {
                        "name": "Model-A",
                        "yaml": "recipes/overlays/org/A.yaml",
                        "hf_id": "org/A",
                        "combos": [{"id": "a5-c", "hardware": "ascend_950dt_8x", "variant": "default", "nodes": 1}],
                    },
                    {
                        "name": "Model-B",
                        "yaml": "recipes/overlays/org/B.yaml",
                        "hf_id": "org/B",
                        "combos": [{"id": "a3-c", "hardware": "ascend_910c", "variant": "default", "nodes": 1}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    labels_file = tmp_path / "runner_label.json"
    labels_file.write_text(
        json.dumps(
            {
                "linux-aarch64-a5-4": {"chip": "a5", "npu_num": 4},
                "linux-aarch64-a3-4": {"chip": "a3", "npu_num": 4},
            }
        ),
        encoding="utf-8",
    )
    return ["--map", str(map_file), "--runner-labels", str(labels_file)]


def _fetch_by_hardware(site, hf_id, combo):
    render = A3_RENDER if combo["hardware"] == "ascend_910c" else A5_RENDER
    return dict(render), SOURCE_URL


def _combos_of(out_dir: Path) -> list[str]:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    return sorted(r["combo_id"] for r in manifest["combos"])


def test_main_models_filter_matches_name_and_hf_id(tmp_path, monkeypatch):
    # --models exists so a manual dispatch can select a model without knowing
    # which entry a bare combo ID such as a3-c belongs to.
    common = _two_model_map(tmp_path)
    monkeypatch.setattr(gen, "fetch_render", _fetch_by_hardware)

    assert gen.main(common + ["--out", str(tmp_path / "name"), "--models", "Model-A"]) == 0
    assert _combos_of(tmp_path / "name") == ["a5-c"]

    # hf_id works too, and matching is case-insensitive.
    assert gen.main(common + ["--out", str(tmp_path / "hf"), "--models", "ORG/B"]) == 0
    assert _combos_of(tmp_path / "hf") == ["a3-c"]

    assert gen.main(common + ["--out", str(tmp_path / "both"), "--models", "Model-A, org/B"]) == 0
    assert _combos_of(tmp_path / "both") == ["a3-c", "a5-c"]


def test_main_models_combines_with_hardware_as_and(tmp_path, monkeypatch):
    # Model-B only declares an A3 combo, so A5 + Model-B matches nothing: the
    # filters are an intersection, and a green run of zero combos would hide it.
    common = _two_model_map(tmp_path)
    monkeypatch.setattr(gen, "fetch_render", _fetch_by_hardware)
    out_dir = tmp_path / "conflict"
    with pytest.raises(SystemExit):
        gen.main(common + ["--out", str(out_dir), "--hardware", "a5", "--models", "Model-B"])
    assert not (out_dir / "manifest.json").exists()


def test_main_unknown_model_is_rejected(tmp_path, monkeypatch):
    common = _two_model_map(tmp_path)
    monkeypatch.setattr(gen, "fetch_render", _fetch_by_hardware)
    with pytest.raises(SystemExit):
        gen.main(common + ["--out", str(tmp_path / "typo"), "--models", "Model-Typo"])


def test_main_empty_change_set_without_filters_stays_green(tmp_path, monkeypatch):
    # PRs pass the three filters as empty strings; an empty change set is a
    # normal no-op and must not trip the filter mismatch guard.
    common = _two_model_map(tmp_path)
    monkeypatch.setattr(gen, "fetch_render", _fetch_by_hardware)
    baseline = tmp_path / "base.json"
    baseline.write_text((tmp_path / "map.json").read_text(encoding="utf-8"), encoding="utf-8")
    out_dir = tmp_path / "unchanged"
    rc = gen.main(
        common
        + ["--out", str(out_dir), "--hardware", "", "--combos", "", "--models", "", "--baseline-map", str(baseline)]
    )
    assert rc == 0
    assert _combos_of(out_dir) == []


def test_changed_combo_ids_preserves_unchanged_models():
    baseline = {"site": "https://recipes.vllm.ai", "entries": [dict(A5_ENTRY, combos=[A5_COMBO])]}
    current = json.loads(json.dumps(baseline))
    assert gen.changed_combo_ids(current, baseline) == set()
    current["entries"].append({"hf_id": "Qwen/Qwen3-VL-Reranker-2B", "combos": [{"id": "reranker"}]})
    assert gen.changed_combo_ids(current, baseline) == {"reranker"}
    current["entries"][0]["combos"][0]["expect"]["dp"] = 2
    assert gen.changed_combo_ids(current, baseline) == {"reranker", A5_COMBO["id"]}


def test_changed_combo_ids_includes_entry_and_global_changes():
    baseline = {"site": "https://recipes.vllm.ai", "entries": [dict(A5_ENTRY, combos=[A5_COMBO])]}
    current = json.loads(json.dumps(baseline))
    current["entries"][0]["hf_id"] = "other/model"
    assert gen.changed_combo_ids(current, baseline) == {A5_COMBO["id"]}
    current = json.loads(json.dumps(baseline))
    current["site"] = "https://preview.example"
    assert gen.changed_combo_ids(current, baseline) == {A5_COMBO["id"]}


def test_main_selects_changed_or_explicit_combos(tmp_path, monkeypatch):
    baseline = {"schema_version": 1, "entries": [dict(A5_ENTRY, combos=[A5_COMBO])]}
    current = json.loads(json.dumps(baseline))
    current["entries"][0]["combos"].append(dict(A5_COMBO, id="new-combo"))
    source, base, labels = (tmp_path / name for name in ("map.json", "base.json", "labels.json"))
    source.write_text(json.dumps(current), encoding="utf-8")
    base.write_text(json.dumps(baseline), encoding="utf-8")
    labels.write_text(json.dumps(RUNNER_LABELS), encoding="utf-8")
    fetched = []

    def fetch(site, hf_id, combo):
        fetched.append(combo["id"])
        return dict(A5_RENDER), SOURCE_URL

    monkeypatch.setattr(gen, "fetch_render", fetch)
    common = ["--map", str(source), "--runner-labels", str(labels)]
    assert gen.main(common + ["--out", str(tmp_path / "changed"), "--baseline-map", str(base)]) == 0
    assert fetched == ["new-combo"]
    fetched.clear()
    assert gen.main(common + ["--out", str(tmp_path / "explicit"), "--combos", A5_COMBO["id"]]) == 0
    assert fetched == [A5_COMBO["id"]]
    with pytest.raises(SystemExit):
        gen.main(common + ["--out", str(tmp_path / "invalid"), "--combos", "typo"])
    assert fetched == [A5_COMBO["id"]]


def test_plan_combo_carries_rerank_smoke_and_required_policy():
    meta = _plan(A5_RENDER, dict(A5_COMBO, smoke_test="rerank", required=True))
    assert meta["smoke_test"] == "rerank"
    assert meta["required"] is True
    with pytest.raises(gen.ComboFailure, match="smoke_test"):
        _plan(A5_RENDER, dict(A5_COMBO, smoke_test="typo"))


def test_required_combo_without_runner_fails_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "fetch_render", lambda *args: (dict(A5_RENDER), SOURCE_URL))
    combo = dict(A5_COMBO, required=True)
    row = gen.process_combo(A5_ENTRY, combo, "https://recipes.vllm.ai", {}, REWRITES, tmp_path)
    assert row["status"] == "skipped"
    assert gen.write_outputs(tmp_path, [row]) == 1


def test_pooling_uses_serving_parallelism_instead_of_full_node_docker_mounts():
    render = dict(A3_RENDER)
    render["argv"] = ["vllm", "serve", "Qwen/Qwen3-VL-Reranker-2B", "--runner", "pooling", "--tensor-parallel-size", "1"]
    render["env"] = {}
    render["docker_argv"] = ["docker", "run"] + [f"/dev/davinci{i}" for i in range(16)]
    meta = _plan(render, dict(A5_COMBO, hardware="ascend_910c", expect={"tp": 1, "dp": 1}))
    assert meta["required_npu"] == 1
    assert meta["runner"] == "linux-aarch64-a3-2"
