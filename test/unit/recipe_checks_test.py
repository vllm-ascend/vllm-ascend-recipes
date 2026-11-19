from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/_scripts"))
from recipe_checks import install_http_probe, record_response, shell_wrapper, validate_response, verification_blocks


class RecipeCheckTests(unittest.TestCase):
    def test_log_fences_are_not_executed_and_all_request_fences_are_kept(self):
        text = "```bash\nINFO: Server ready\n```\n```bash\ncurl http://localhost:8000/v1/embeddings\n```\n```shell\ncurl http://localhost:8000/score\n```"
        self.assertEqual(verification_blocks(text), ["curl http://localhost:8000/v1/embeddings", "curl http://localhost:8000/score"])

    def test_python_request_example_is_an_executable_check(self):
        blocks = verification_blocks("```python\nimport requests\nrequests.post('http://localhost:8000/score', json={})\n```")
        self.assertEqual(len(blocks), 1)
        self.assertIn("requests.post", blocks[0])

    def test_empty_json_and_api_errors_are_never_task_success(self):
        for payload in ({}, {"error": {"message": "unsupported"}}, {"choices": []}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_response("http://localhost/v1/chat/completions", json.dumps(payload))

    def test_text_chat_reasoning_and_completion_responses(self):
        cases = [
            ("/v1/chat/completions", {"choices": [{"message": {"content": "Hello"}}]}),
            ("/v1/chat/completions", {"choices": [{"message": {"reasoning_content": "Reasoning", "content": "Answer"}}]}),
            ("/v1/completions", {"choices": [{"text": "Hello"}]}),
        ]
        for endpoint, body in cases:
            with self.subTest(endpoint=endpoint):
                self.assertTrue(validate_response("http://localhost" + endpoint, json.dumps(body)))

    def test_reasoning_without_a_final_answer_is_not_a_chat_result(self):
        for finish_reason in ("length", "stop", None):
            body = {"choices": [{"message": {"content": None, "reasoning_content": "Still thinking"},
                                 "finish_reason": finish_reason}]}
            with self.subTest(finish_reason=finish_reason), self.assertRaises(ValueError):
                validate_response("http://localhost/v1/chat/completions", json.dumps(body))

    def test_streamed_reasoning_needs_final_content_or_tool_calls(self):
        reasoning = 'data: {"choices":[{"delta":{"reasoning_content":"Thinking"}}]}\n\n'
        end = 'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n'
        with self.assertRaises(ValueError):
            validate_response("http://localhost/v1/chat/completions", reasoning + end)
        content = 'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n'
        self.assertTrue(validate_response("http://localhost/v1/chat/completions", reasoning + content + end))

    def test_embedding_score_rerank_and_asr_response_shapes(self):
        cases = [
            ("/v1/embeddings", {"data": [{"embedding": [0.1, 0.2]}]}),
            ("/score", {"data": [{"score": 0.7}]}),
            ("/rerank", {"results": [{"index": 0, "relevance_score": 0.7}]}),
            ("/v1/audio/transcriptions", {"text": "Hello"}),
        ]
        for endpoint, body in cases:
            with self.subTest(endpoint=endpoint):
                self.assertTrue(validate_response("http://localhost" + endpoint, json.dumps(body)))

    def test_invalid_task_payloads_are_rejected(self):
        for endpoint, body in [("/v1/embeddings", {"data": []}), ("/score", {"data": [{"score": "bad"}]}),
                               ("/v1/audio/transcriptions", {"text": ""})]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_response("http://localhost" + endpoint, json.dumps(body))

    def test_malformed_array_members_are_validation_errors_not_unrecorded_crashes(self):
        for endpoint, body in [("/v1/chat/completions", {"choices": [None]}),
                               ("/v1/embeddings", {"data": [None]}), ("/score", {"data": "bad"})]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_response("http://localhost" + endpoint, json.dumps(body))

    def test_request_to_a_different_service_cannot_verify_the_local_model(self):
        with patch.dict(os.environ, {"CI_SERVER_PORT": "8123"}):
            self.assertTrue(validate_response("http://localhost:8123/score", '{"data":[{"score":0.9}]}'))
            with self.assertRaises(ValueError):
                validate_response("http://localhost:8000/score", '{"data":[{"score":0.9}]}')
            with self.assertRaises(ValueError):
                validate_response("https://example.invalid:8123/score", '{"data":[{"score":0.9}]}')

    def test_models_readiness_is_not_task_execution_evidence(self):
        self.assertFalse(validate_response("http://localhost/v1/models", '{"data": [{"id": "model"}]}'))

    def test_python_heredoc_runner_executes_the_original_script(self):
        result = subprocess.run([sys.executable, str(ROOT / ".github/_scripts/recipe_checks.py"), "--run-python", "-"],
                                input="print('request-script-executed')\n", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "request-script-executed")

    def test_python_fence_preserves_future_imports(self):
        blocks = verification_blocks("```python\nfrom __future__ import annotations\nimport urllib.request\nprint('future-import-kept')\n```")
        result = subprocess.run(["bash", "-e", "-c", blocks[0]], capture_output=True, text=True,
                                env={**os.environ, "PYTHON": sys.executable.replace("\\", "/"),
                                     "RECIPE_CHECKS_SCRIPT": str(ROOT / ".github/_scripts/recipe_checks.py")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "future-import-kept")

    def test_python_runner_preserves_interpreter_flags(self):
        for flags in [["-u"], ["-W", "ignore"], ["-u", "-Wignore"]]:
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, str(ROOT / ".github/_scripts/recipe_checks.py"),
                                         "--run-python", *flags, "-"], input="print('flags-kept')\n",
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "flags-kept")

    def test_swallowed_validation_failure_cannot_be_hidden_by_an_earlier_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / "proof.jsonl"
            with patch.dict(os.environ, {"CI_REQUEST_PROOF_FILE": str(proof)}):
                record_response("http://localhost/score", '{"data":[{"score":0.8}]}')
                try:
                    record_response("http://localhost/score", '{"error":"bad request"}')
                except ValueError:
                    pass
            result = subprocess.run([sys.executable, str(ROOT / ".github/_scripts/recipe_checks.py"), "--check-proof", str(proof)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_urllib_probe_preserves_the_body_for_the_recipe_assertions(self):
        import urllib.request
        payload = b'{"data":[{"score":0.9}]}'
        with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
            install_http_probe()
            self.assertEqual(urllib.request.urlopen("http://localhost/score").read(), payload)

    def test_shell_wrapper_rejects_200_error_json_and_records_valid_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "bin/curl").write_text(
                '#!/usr/bin/env bash\nwhile [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then output=$2; shift 2; else shift; fi; done\nprintf "%s" "$FAKE_BODY" > "$output"\nexit 0\n',
                encoding="utf-8", newline="\n",
            )
            proof = root / "proof.jsonl"
            script = 'export PATH="$PWD/bin:/usr/bin:/bin:$PATH"\nchmod +x bin/curl\n' + shell_wrapper() + '\ncurl http://localhost/v1/embeddings\n'
            env = {**os.environ, "PYTHON": sys.executable.replace("\\", "/"),
                   "RECIPE_CHECKS_SCRIPT": str(ROOT / ".github/_scripts/recipe_checks.py"),
                   "CI_REQUEST_PROOF_FILE": str(proof)}
            for body, passed in [('{"error":"unsupported"}', False), ('{"data":[{"embedding":[0.1,0.2]}]}', True)]:
                with self.subTest(body=body):
                    proof.write_text("", encoding="utf-8")
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=root,
                                            env={**env, "FAKE_BODY": body}, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, passed, result.stderr)
                    records = [json.loads(line) for line in proof.read_text().splitlines()]
                    self.assertEqual([record["ok"] for record in records], [passed])


if __name__ == "__main__":
    unittest.main()
