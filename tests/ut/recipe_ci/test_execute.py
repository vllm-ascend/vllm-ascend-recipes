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

"""Unit tests for the recipe CI runner (recipes/ci/execute.py)."""

import importlib.util
import json
import shlex
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXECUTE_PATH = _REPO_ROOT / "recipes" / "ci" / "execute.py"

_spec = importlib.util.spec_from_file_location("recipe_ci_execute", _EXECUTE_PATH)
exe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exe)


# --- local model resolution -------------------------------------------------


def test_local_model_dir_modelscope(tmp_path, monkeypatch):
    model = tmp_path / "hub" / "models" / "org" / "model"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    assert exe.local_model_dir("org/model", "modelscope") == model


def test_local_model_dir_modelscope_missing_config(tmp_path, monkeypatch):
    model = tmp_path / "hub" / "models" / "org" / "model"
    model.mkdir(parents=True)
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    # Directory exists but no config.json: not directly servable.
    assert exe.local_model_dir("org/model", "modelscope") is None


def test_local_model_dir_huggingface_snapshot(tmp_path, monkeypatch):
    snapshots = tmp_path / "hub" / "models--org--model" / "snapshots" / "abc123"
    snapshots.mkdir(parents=True)
    (snapshots / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert exe.local_model_dir("org/model", "huggingface") == snapshots


def test_local_model_dir_huggingface_empty(tmp_path, monkeypatch):
    (tmp_path / "hub" / "models--org--model" / "snapshots").mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert exe.local_model_dir("org/model", "huggingface") is None


# --- run.sh localization ----------------------------------------------------


def _make_combo(tmp_path, monkeypatch, model_id="Eco-Tech/model-w8a8", hub="modelscope"):
    model = tmp_path / "cache" / "hub" / "models" / "Eco-Tech" / "model-w8a8"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("HF_HOME", raising=False)

    combo_dir = tmp_path / "combo" / "a3-test"
    combo_dir.mkdir(parents=True)
    (combo_dir / "run.sh").write_text(
        "export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3\n"
        f"exec vllm serve {model_id} --tensor-parallel-size 4 "
        f"--speculative-config '{{\"method\":\"eagle3\",\"model\":\"{model_id}\"}}'\n",
        encoding="utf-8",
    )
    meta = {
        "model_id": model_id,
        "draft_model": model_id,
        "hub": hub,
    }
    return combo_dir, meta


def test_localized_run_sh_rewrites_hub_ids(tmp_path, monkeypatch, capsys):
    combo_dir, meta = _make_combo(tmp_path, monkeypatch)
    out_dir = tmp_path / "results" / "a3-test"
    out_dir.mkdir(parents=True)

    target = exe.localized_run_sh(combo_dir, out_dir, meta)
    script = target.read_text(encoding="utf-8")
    local = str(tmp_path / "cache" / "hub" / "models" / "Eco-Tech" / "model-w8a8")
    # The serve argument and the id inside --speculative-config are rewritten.
    assert f"vllm serve {local}" in script
    assert f'"model":"{local}"' in script
    assert "Eco-Tech/model-w8a8" not in script
    # The generated artifact is untouched.
    assert "Eco-Tech/model-w8a8" in (combo_dir / "run.sh").read_text(encoding="utf-8")
    # The rewrite is logged.
    assert "model_id: Eco-Tech/model-w8a8 ->" in capsys.readouterr().out


def test_localized_run_sh_keeps_unknown_ids(tmp_path, monkeypatch):
    combo_dir, meta = _make_combo(tmp_path, monkeypatch, model_id="org/not-cached-anywhere")
    out_dir = tmp_path / "results" / "a3-test"
    out_dir.mkdir(parents=True)

    target = exe.localized_run_sh(combo_dir, out_dir, meta)
    # No cached copy exists: the id is left exactly as rendered.
    script = target.read_text(encoding="utf-8")
    assert "org/not-cached-anywhere" in script


# --- resolved serve command log ----------------------------------------------


def test_format_serve_command_env_prefix_and_continuations(tmp_path, monkeypatch):
    model = tmp_path / "cache" / "hub" / "models" / "Eco-Tech" / "model-w8a8"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path / "cache"))
    meta = {
        "model_id": "Eco-Tech/model-w8a8",
        "draft_model": None,
        "hub": "modelscope",
        "env": {"HCCL_BUFFSIZE": "1024", "ASCEND_RT_VISIBLE_DEVICES": "0,1"},
        "argv": [
            "vllm",
            "serve",
            "Eco-Tech/model-w8a8",
            "--trust-remote-code",
            "--data-parallel-size",
            "4",
            "--compilation-config",
            '{"cudagraph_mode":"FULL_DECODE_ONLY"}',
            "--tensor-parallel-size",
            "4",
        ],
    }
    lines = exe.format_serve_command(meta).splitlines()
    assert lines[0] == "export ASCEND_RT_VISIBLE_DEVICES=0,1"  # env sorted, prefixed
    assert lines[1] == "export HCCL_BUFFSIZE=1024"
    # Local weights path, not the hub id; bare flags stand on their own line.
    assert lines[2] == f"vllm serve {shlex.quote(str(model))} \\"
    assert lines[3] == "    --trust-remote-code \\"
    assert lines[4] == "    --data-parallel-size 4 \\"
    assert lines[5] == '    --compilation-config \'{"cudagraph_mode":"FULL_DECODE_ONLY"}\' \\'
    assert lines[6] == "    --tensor-parallel-size 4"  # last line: no continuation


def test_format_serve_command_rewrites_nested_draft_model_id(tmp_path, monkeypatch):
    for name in ("draft", "main"):
        d = tmp_path / "cache" / "hub" / "models" / "org" / name
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path / "cache"))
    meta = {
        "model_id": "org/main",
        "draft_model": "org/draft",
        "hub": "modelscope",
        "env": {},
        "argv": [
            "vllm",
            "serve",
            "org/main",
            "--speculative-config",
            '{"method":"eagle3","model":"org/draft"}',
        ],
    }
    main_local = tmp_path / "cache" / "hub" / "models" / "org" / "main"
    draft_local = tmp_path / "cache" / "hub" / "models" / "org" / "draft"
    block = exe.format_serve_command(meta)
    assert "org/main" not in block and "org/draft" not in block
    assert str(main_local) in block
    # The draft id nested inside the --speculative-config JSON is rewritten too.
    assert f'"model":"{draft_local}"' in block
