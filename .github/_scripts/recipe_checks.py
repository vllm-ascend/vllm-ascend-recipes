"""Extract executable recipe checks and validate task-level API evidence."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
from pathlib import Path
import re
import runpy
import sys
from urllib.parse import urlparse


def verification_blocks(markdown: str) -> list[str]:
    checks = []
    for language, body in re.findall(r"```(bash|shell|python|python3)\s*\n(.*?)```", markdown, re.DOTALL):
        executable = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        if not re.search(r"\bcurl\b|\brequests\.(?:post|get|request)|urllib\.request|\burlopen\(", executable):
            continue  # INFO/output examples are documentation, not commands.
        body = body.strip()
        if language.startswith("python"):
            body = '${PYTHON:-python3} "$RECIPE_CHECKS_SCRIPT" --run-python - <<\'RECIPE_PY_CHECK\'\n' + body + "\nRECIPE_PY_CHECK"
        checks.append(body)
    return checks


def endpoint_kind(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return {"/v1/chat/completions": "chat", "/v1/completions": "completion",
            "/v1/embeddings": "embedding", "/score": "score", "/v1/score": "score",
            "/rerank": "rerank", "/v1/rerank": "rerank",
            "/v1/audio/transcriptions": "asr"}.get(path, "")


def validate_response(url: str, body: str) -> bool:
    kind = endpoint_kind(url)
    if not kind:
        return False  # /v1/models and /health are readiness, not inference.
    expected_port = os.environ.get("CI_SERVER_PORT")
    if expected_port:
        target = urlparse(url)
        port = target.port or (443 if target.scheme == "https" else 80)
        if target.hostname not in {"localhost", "127.0.0.1", "::1"} or port != int(expected_port):
            raise ValueError("task request did not target the local CI model server")
    if body.lstrip().startswith("data:"):
        frames = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        payloads = [json.loads(frame) for frame in frames if frame and frame != "[DONE]"]
    else:
        payloads = [json.loads(body)]
    valid = False
    for data in payloads:
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("API returned an error or a non-object response")
        if kind in {"chat", "completion"}:
            for choice in _object_rows(data.get("choices", [])):
                message = choice.get("message") or choice.get("delta") or {}
                if not isinstance(message, dict):
                    raise ValueError("chat message must be an object")
                # Reasoning alone is not a final task result (e.g. a request
                # truncated before answering). This also applies to SSE chunks.
                valid = valid or bool(choice.get("text") or message.get("content") or message.get("tool_calls"))
        elif kind == "embedding":
            rows = _object_rows(data.get("data", []))
            valid = bool(rows) and all(isinstance(row.get("embedding"), list) and row["embedding"]
                                      and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                                              for v in row["embedding"]) for row in rows)
        elif kind in {"score", "rerank"}:
            rows = _object_rows(data.get("data", []) if kind == "score" else data.get("results", []))
            values = [row.get("score", row.get("relevance_score")) for row in rows]
            valid = bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                         and math.isfinite(v) for v in values)
        else:
            valid = isinstance(data.get("text"), str) and bool(data["text"].strip())
    if not valid:
        raise ValueError(f"{kind} response has no valid task result")
    return True


def _object_rows(value: object) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("task results must be an array of objects")
    return value


def record_response(url: str, body: str = "", *, failed: bool = False) -> None:
    if not endpoint_kind(url):
        return
    try:
        if failed:
            raise ValueError("HTTP request failed")
        validate_response(url, body)
    except Exception as error:
        _record(url, False)
        raise ValueError(str(error)) from error
    _record(url, True)


def _record(url: str, ok: bool) -> None:
    proof = os.environ.get("CI_REQUEST_PROOF_FILE")
    if proof:
        with open(proof, "a", encoding="utf-8") as output:
            output.write(json.dumps({"endpoint": urlparse(url).path, "ok": ok}) + "\n")


def install_http_probe() -> None:
    """Instrument only the verification process, never the vLLM server."""
    import urllib.request
    original = urllib.request.urlopen

    def checked_urlopen(request, *args, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        try:
            response = original(request, *args, **kwargs)
            if endpoint_kind(url):
                body = response.read()
                record_response(url, body.decode("utf-8"))
                response.read = io.BytesIO(body).read
            return response
        except Exception:
            if endpoint_kind(url):
                _record(url, False)
            raise

    urllib.request.urlopen = checked_urlopen
    try:
        import requests
    except ImportError:
        return
    request = requests.sessions.Session.request

    def checked_request(session, method, url, *args, **kwargs):
        try:
            response = request(session, method, url, *args, **kwargs)
            if endpoint_kind(url):
                response.raise_for_status()
                record_response(url, response.text)
            return response
        except Exception:
            if endpoint_kind(url):
                _record(url, False)
            raise

    requests.sessions.Session.request = checked_request


def shell_wrapper() -> str:
    return r'''
curl() {
  local url="" arg body rc
  for arg in "$@"; do
    case "$arg" in http://*|https://*) url="$arg"; break ;; esac
  done
  case "$url" in
    */v1/chat/completions*|*/v1/completions*|*/v1/embeddings*|*/score*|*/rerank*|*/v1/score*|*/v1/rerank*|*/v1/audio/transcriptions*) ;;
    *) command curl "$@"; return ;;
  esac
  body=$(mktemp)
  if command curl --fail-with-body --silent --show-error "$@" --output "$body"; then
    if "$PYTHON" "$RECIPE_CHECKS_SCRIPT" --url "$url" --body "$body"; then
      cat "$body"; rm -f "$body"; return 0
    fi
    cat "$body"; rm -f "$body"; return 1
  else
    rc=$?
    "$PYTHON" "$RECIPE_CHECKS_SCRIPT" --url "$url" --failed || true
    cat "$body"; rm -f "$body"; return "$rc"
  fi
}
export -f curl
python() { "$PYTHON" "$RECIPE_CHECKS_SCRIPT" --run-python "$@"; }
python3() { "$PYTHON" "$RECIPE_CHECKS_SCRIPT" --run-python "$@"; }
python3.12() { "$PYTHON" "$RECIPE_CHECKS_SCRIPT" --run-python "$@"; }
export -f python python3 python3.12
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="")
    parser.add_argument("--body", type=Path)
    parser.add_argument("--failed", action="store_true")
    parser.add_argument("--shell-wrapper", action="store_true")
    parser.add_argument("--check-proof", type=Path)
    parser.add_argument("--run-python", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.run_python is not None:
            argv = args.run_python
            flags = []
            while argv and argv[0].startswith("-") and argv[0] not in {"-", "-m", "-c"}:
                flag, *argv = argv
                flags.append(flag)
                if flag in {"-W", "-X"}:
                    if not argv:
                        raise ValueError(f"{flag} requires an interpreter option")
                    value, *argv = argv
                    flags.append(value)
            if flags:
                os.execv(sys.executable, [sys.executable, *flags, str(Path(__file__).resolve()), "--run-python", *argv])
            install_http_probe()
            if not argv or argv[0] == "-":
                sys.argv = ["-", *argv[1:]]
                exec(compile(sys.stdin.read(), "<recipe verification>", "exec"), {"__name__": "__main__"})
            elif argv[0] == "-c" and len(argv) >= 2:
                sys.argv = ["-c", *argv[2:]]
                exec(compile(argv[1], "<recipe verification>", "exec"), {"__name__": "__main__"})
            elif argv[0] == "-m" and len(argv) >= 2:
                sys.argv = argv[1:]
                runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
            else:
                sys.argv = argv
                runpy.run_path(argv[0], run_name="__main__")
        elif args.shell_wrapper:
            print(shell_wrapper())
        elif args.check_proof is not None:
            records = [json.loads(line) for line in args.check_proof.read_text(encoding="utf-8").splitlines()]
            if any(not row.get("ok") for row in records):
                raise ValueError("at least one task request failed validation")
            if not records:
                parser.exit(2, "No validated task request was executed\n")
        else:
            record_response(args.url, args.body.read_text(encoding="utf-8") if args.body else "", failed=args.failed)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Recipe check: {error}\n")


if __name__ == "__main__":
    main()
