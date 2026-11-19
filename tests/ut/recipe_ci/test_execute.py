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
    assert shlex.split(script.splitlines()[1])[3] == local
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
    assert shlex.split(block.splitlines()[0].removesuffix(" \\")) == ["vllm", "serve", str(main_local)]
    assert str(main_local) in block
    # The draft id nested inside the --speculative-config JSON is rewritten too.
    assert f'"model":"{draft_local}"' in block


def test_same_model_ids_are_localized_once_on_linux_paths(tmp_path, monkeypatch):
    combo_dir, meta = _make_combo(tmp_path, monkeypatch)
    paths = {"model_id": "/cache/Eco-Tech/model-w8a8", "draft_model": "/cache/Eco-Tech/model-w8a8"}
    monkeypatch.setattr(exe, "localized_model_paths", lambda meta: paths)
    out = tmp_path / "out"
    out.mkdir()
    script = exe.localized_run_sh(combo_dir, out, meta).read_text(encoding="utf-8")
    assert "exec vllm serve /cache/Eco-Tech/model-w8a8 --tensor-parallel-size" in script
    assert '"model":"/cache/Eco-Tech/model-w8a8"' in script
    assert "/cache//cache/" not in script


def test_overlapping_model_ids_do_not_rewrite_local_paths(monkeypatch):
    paths = {"model_id": "/cache/org/model", "draft_model": "/cache/org/model-draft"}
    monkeypatch.setattr(exe, "localized_model_paths", lambda meta: paths)
    meta = {"model_id": "org/model", "draft_model": "org/model-draft", "env": {},
            "argv": ["vllm", "serve", "org/model", "--speculative-config", '{"model":"org/model-draft"}']}
    block = exe.format_serve_command(meta)
    assert shlex.split(block.splitlines()[0].removesuffix(" \\")) == ["vllm", "serve", "/cache/org/model"]
    config = json.loads(shlex.split(block.splitlines()[-1])[1])
    assert config["model"] == "/cache/org/model-draft"


def test_rerank_smoke_uses_tutorial_request(monkeypatch):
    requests = []

    def post(url, payload):
        requests.append((url, payload))
        return {"results": [{"index": 0, "relevance_score": 0.99}, {"index": 1, "relevance_score": 0.1}]}

    monkeypatch.setattr(exe, "http_post_json", post)
    exe.smoke("http://127.0.0.1:8000", "Qwen/Qwen3-VL-Reranker-2B", "rerank")
    url, payload = requests[0]
    assert url == "http://127.0.0.1:8000/v1/rerank"
    assert payload["model"] == "Qwen/Qwen3-VL-Reranker-2B"
    assert payload["query"] == "What is the capital of China?"
    assert len(payload["documents"]) == 2
    assert "messages" not in payload


@pytest.mark.parametrize("results", [
    [],
    [{"index": 0, "relevance_score": 0.9}],
    [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}],
    [{"index": 0, "relevance_score": float("nan")}, {"index": 1, "relevance_score": 0.1}],
    [{"index": 0, "relevance_score": float("inf")}, {"index": 1, "relevance_score": 0.1}],
    [{"index": 0, "relevance_score": "high"}, {"index": 1, "relevance_score": 0.1}],
    [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}],
    [{"index": 0, "relevance_score": 0.9}, {"index": 2, "relevance_score": 0.1}],
])
def test_rerank_smoke_rejects_invalid_or_reversed_scores(monkeypatch, results):
    monkeypatch.setattr(exe, "http_post_json", lambda *args: {"results": results})
    with pytest.raises(exe.ComboFailure):
        exe.smoke("http://127.0.0.1:8000", "model", "rerank")


def test_default_smoke_still_checks_chat_content(monkeypatch):
    requests = []
    monkeypatch.setattr(exe, "http_post_json", lambda url, payload: requests.append(url) or {"choices": [{"message": {"content": "ok"}}]})
    exe.smoke("http://127.0.0.1:8000", "model")
    assert requests == ["http://127.0.0.1:8000/v1/chat/completions"]


# --- cURL verification / vllm bench serve (recipes site snippets) ------------


def test_curl_command_matches_site_snippet():
    assert exe.curl_command("http://127.0.0.1:8100", "Eco-Tech/model-w8a8") == (
        "curl http://127.0.0.1:8100/v1/chat/completions \\\n"
        '  -H "Content-Type: application/json" \\\n'
        "  -d '{\n"
        '    "model": "Eco-Tech/model-w8a8",\n'
        '    "messages": [{"role": "user", "content": "Hello"}],\n'
        '    "max_tokens": 32\n'
        "  }'"
    )


def _curl_run(monkeypatch, calls, stdout="{}", stderr="", returncode=0):
    """Stub subprocess.run so verify_service's curl call is captured, not run."""

    def run(argv, **kwargs):
        calls.append(argv)
        proc = type("Proc", (), {})()
        proc.stdout, proc.stderr, proc.returncode = stdout, stderr, returncode
        return proc

    monkeypatch.setattr(exe.subprocess, "run", run)


def test_verify_service_runs_the_page_curl_and_reports_the_response(monkeypatch, capsys):
    calls = []
    body = json.dumps({"choices": [{"message": {"content": "Hello!"}}]})
    _curl_run(monkeypatch, calls, stdout=body)

    exe.verify_service("http://127.0.0.1:8100", "Eco-Tech/model-w8a8")

    argv = calls[0]
    assert argv[:2] == ["curl", "http://127.0.0.1:8100/v1/chat/completions"]
    assert argv[2:4] == ["-H", "Content-Type: application/json"]
    assert argv[4] == "-d"
    assert json.loads(argv[5]) == {
        "model": "Eco-Tech/model-w8a8",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 32,
    }
    out = capsys.readouterr().out
    # The page snippet is echoed, and the real response is now reported.
    assert "curl http://127.0.0.1:8100/v1/chat/completions \\" in out
    assert '"content": "Hello!"' in out


@pytest.mark.parametrize("resp", [{}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}])
def test_verify_service_rejects_empty_content(monkeypatch, resp):
    _curl_run(monkeypatch, [], stdout=json.dumps(resp))
    with pytest.raises(exe.ComboFailure):
        exe.verify_service("http://127.0.0.1:8100", "model")


def test_verify_service_fails_on_non_zero_curl_exit(monkeypatch):
    _curl_run(monkeypatch, [], stderr="curl: (7) Failed to connect", returncode=7)
    with pytest.raises(exe.ComboFailure, match="exit 7"):
        exe.verify_service("http://127.0.0.1:8100", "model")


def test_bench_command_matches_site_snippet():
    assert exe.bench_command("http://127.0.0.1:8100", "Eco-Tech/model-w8a8") == [
        "vllm", "bench", "serve",
        "--model", "Eco-Tech/model-w8a8",
        "--host", "127.0.0.1",
        "--port", "8100",
        "--dataset-name", "random",
        "--random-input-len", "1024",
        "--random-output-len", "1024",
        "--num-prompts", "100",
        "--max-concurrency", "32",
    ]


def test_parse_bench_metrics_reads_the_result_table():
    text = (
        "============ Serving Benchmark Result ============\n"
        "Successful requests:                     100\n"
        "Benchmark duration (s):                  22.35\n"
        "Request throughput (req/s):              4.47\n"
        "Output token throughput (tok/s):         4578.75\n"
        "---------------Time to First Token----------------\n"
        "Mean TTFT (ms):                          71.23\n"
        "Device: NPU\n"
    )
    metrics = exe.parse_bench_metrics(text)
    assert metrics["Successful requests"] == 100.0
    assert metrics["Output token throughput (tok/s)"] == 4578.75
    assert metrics["Mean TTFT (ms)"] == 71.23
    assert "Device" not in metrics


def _fake_bench(monkeypatch, returncode=0, stdout="", stderr=""):
    class Proc:
        pass

    proc = Proc()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    monkeypatch.setattr(exe.subprocess, "run", lambda *args, **kwargs: proc)


def test_run_bench_writes_log_and_returns_metrics(tmp_path, monkeypatch, capsys):
    _fake_bench(monkeypatch, stdout="Output token throughput (tok/s):         4578.75\n")
    result = exe.run_bench("http://127.0.0.1:8100", "model", tmp_path)
    assert result["metrics"]["Output token throughput (tok/s)"] == 4578.75
    assert "Output token throughput" in (tmp_path / "bench.log").read_text(encoding="utf-8")
    assert "--max-concurrency 32" in capsys.readouterr().out


@pytest.mark.parametrize(
    "returncode,stdout",
    [(1, "Output token throughput (tok/s): 1\n"), (0, "no table here\n")],
)
def test_run_bench_failures_mark_the_combo_red(tmp_path, monkeypatch, returncode, stdout):
    _fake_bench(monkeypatch, returncode=returncode, stdout=stdout, stderr="boom")
    with pytest.raises(exe.ComboFailure):
        exe.run_bench("http://127.0.0.1:8100", "model", tmp_path)
    # The raw output is kept for the artifacts even on failure.
    assert (tmp_path / "bench.log").exists()


@pytest.mark.parametrize("required,expected", [(True, 1), (False, 0)])
def test_required_combo_skip_is_not_a_passing_check(tmp_path, monkeypatch, required, expected):
    meta = {"combo_id": "reranker", "hf_id": "Qwen/Qwen3-VL-Reranker-2B", "hardware": "ascend_910c",
            "variant": "default", "features_resolved": {}, "image": "registry/image:1", "strategy": "single_node_tp",
            "deploy_type": "single_node", "required_npu": 1, "port": 8000, "source_url": "https://recipes.vllm.ai",
            "required": required, "smoke_test": "rerank"}
    combo_dir = tmp_path / "combo"
    combo_dir.mkdir()
    (combo_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(exe, "precheck", lambda meta: "weights not cached")
    assert exe.run_combo(combo_dir, tmp_path / "results") == expected
    result = json.loads((tmp_path / "results/reranker/result.json").read_text(encoding="utf-8"))
    assert result["status"] == "skip"


# --- run_combo wiring (verify -> smoke -> bench -> teardown) ----------------


class _FakeProc:
    pid = 4242

    def poll(self):
        return None


def _combo_meta(tmp_path, **over):
    meta = {
        "combo_id": "a3-chat", "hf_id": "org/model", "hardware": "ascend_910c",
        "variant": "default", "features_resolved": {}, "image": "registry/image:1",
        "strategy": "single_node_tp", "deploy_type": "single_node", "required_npu": 1,
        "port": 8100, "source_url": "https://recipes.vllm.ai", "required": False,
        "smoke_test": "chat", "bench": True, "model_id": "org/model",
        "draft_model": None, "hub": "modelscope", "env": {},
        "argv": ["vllm", "serve", "org/model"], "readiness_timeout_s": 60, "companions": [],
    }
    meta.update(over)
    combo_dir = tmp_path / "combo"
    combo_dir.mkdir(parents=True, exist_ok=True)
    (combo_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (combo_dir / "run.sh").write_text("exec vllm serve org/model\n", encoding="utf-8")
    return combo_dir, meta


def _stub_run_combo(monkeypatch, calls):
    monkeypatch.setattr(exe, "precheck", lambda meta: None)
    monkeypatch.setattr(exe, "start_bg", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(exe, "wait_ready", lambda *args, **kwargs: [{"id": "served-id"}])
    monkeypatch.setattr(exe, "verify_service", lambda url, model: calls.append(("verify", url, model)))
    monkeypatch.setattr(exe, "smoke", lambda url, model, kind: calls.append(("smoke", model, kind)))
    monkeypatch.setattr(
        exe, "run_bench",
        lambda url, model, out: calls.append(("bench", model)) or {"seconds": 1.0, "metrics": {}},
    )
    monkeypatch.setattr(exe, "teardown", lambda procs, out: calls.append(("teardown", len(procs))))


def test_run_combo_runs_verify_smoke_and_bench(tmp_path, monkeypatch):
    combo_dir, _ = _combo_meta(tmp_path)
    calls = []
    _stub_run_combo(monkeypatch, calls)

    assert exe.run_combo(combo_dir, tmp_path / "results") == 0
    # The id /v1/models reports feeds every request: the localized run.sh served
    # the on-disk path, so the hub id must not leak into the checks.
    assert calls == [
        ("verify", "http://127.0.0.1:8100", "served-id"),
        ("smoke", "served-id", "chat"),
        ("bench", "served-id"),
        ("teardown", 1),
    ]
    result = json.loads((tmp_path / "results/a3-chat/result.json").read_text(encoding="utf-8"))
    assert result["status"] == "pass"
    assert result["verify_seconds"] is not None
    assert result["bench"] == {"seconds": 1.0, "metrics": {}}


def test_run_combo_skips_bench_and_curl_for_rerank(tmp_path, monkeypatch):
    combo_dir, _ = _combo_meta(tmp_path, smoke_test="rerank", bench=False)
    calls = []
    _stub_run_combo(monkeypatch, calls)

    assert exe.run_combo(combo_dir, tmp_path / "results") == 0
    assert calls == [("smoke", "served-id", "rerank"), ("teardown", 1)]
    result = json.loads((tmp_path / "results/a3-chat/result.json").read_text(encoding="utf-8"))
    assert result["bench"] is None
