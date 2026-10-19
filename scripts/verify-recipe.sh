#!/usr/bin/env bash
# verify-recipe.sh �?Run a recipe's vllm serve commands and verify the service.
#
# Usage:
#   ./scripts/verify-recipe.sh models/en/Qwen/Qwen3-30B-A3B.yaml
#
# Exit codes:
#   0 �?all scenarios verified successfully
#   1 �?one or more scenario failed
#   2 �?recipe skipped (no compatible hardware / unsupported)
set -euo pipefail

RECIPE="$1"
STATUS=0
SKIPPED=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RESET='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${RESET}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*"; }

if [[ ! -f "$RECIPE" ]]; then
  log_error "Recipe file not found: $RECIPE"
  exit 1
fi

# Resolve the cache-paths alias file relative to this script's own location
# so it works in CI (working-directory=./recipes) and locally. Override via
# CACHE_PATHS_FILE for unusual layouts.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export RECIPE_REPO_ROOT="${SCRIPT_DIR}/.."
export CACHE_PATHS_FILE="${CACHE_PATHS_FILE:-${SCRIPT_DIR}/../models/_cache_paths.yaml}"
if [[ ! -f "$CACHE_PATHS_FILE" ]]; then
  log_error "Cache alias file not found: $CACHE_PATHS_FILE"
  exit 1
fi

# Determine Python to use (container may have multiple versions)
PYTHON=$(command -v python3.12 || command -v python3)
log_info "Using Python: $PYTHON ($($PYTHON --version 2>&1))"
log_info "=== Verifying recipe: $RECIPE ==="

# Determine which NPU hardware is available on this runner
# Use RECIPE_HW_KEY env var if set, otherwise default to atlas_800_a2
HW_KEY="${RECIPE_HW_KEY:-atlas_800_a2}"
log_info "Hardware key: $HW_KEY"

# Quick check that NPU is accessible
npu-smi info 2>/dev/null > /dev/null || {
  log_error "npu-smi not accessible. Is ascend-toolkit sourced?"
  exit 2
}

# Parse YAML with Python helper
parse_recipe() {
  $PYTHON - "$RECIPE" "$HW_KEY" "$CACHE_PATHS_FILE" <<'PYEOF'
import sys
import os
import yaml
import json
import re
import shlex

sys.path.insert(0, os.path.join(os.environ['RECIPE_REPO_ROOT'], '.github', '_scripts'))
from recipe_checks import verification_blocks

recipe_path, hw_key, cache_paths_file = sys.argv[1], sys.argv[2], sys.argv[3]

with open(recipe_path, 'r') as f:
    data = yaml.safe_load(f)

meta = data.get('meta', {})
hardware = meta.get('hardware', {})

# Check if recipe supports this hardware
hw_status = hardware.get(hw_key, None)
if hw_status == 'unsupported':
    print(json.dumps({'action': 'skip', 'reason': f'Recipe marks {hw_key} as unsupported'}))
    sys.exit(0)

# Extract env_setup (pip install)
env_setup = data.get('env_setup', {})
pip_content = env_setup.get('pip', {}).get('content', '')
hw_to_container = {'atlas_800_a2': 'A2', 'atlas_800_a3': 'A3', 'ascend_950dt': 'A5'}
container_content = env_setup.get('container', {}).get(hw_to_container.get(hw_key, 'A2'), {}).get('content', '')

# Extract global verification (curl commands shared across scenarios)
verification = data.get('verification', '')
global_verify_cmds = verification_blocks(verification)

# Resolve {{name}} placeholders exactly like the site's Config panel:
#   - config_params (value params) -> its `default`
#   - features (boolean toggles)   -> args/env when ON, flag_when_false when OFF;
#     default state derives from opt_in_features (absent = ON)
def _render_arg(a):
    return "'%s'" % a if re.search(r'[\s{}]', a) and not re.match(r"^['\"]", a) else a

def resolve_placeholders(content, data):
    features = data.get('features') or {}
    config_params = data.get('config_params') or {}
    opt_in = set(data.get('opt_in_features') or [])

    def render_feature(f, on):
        if not on:
            return f.get('flag_when_false', '')
        parts = []
        if f.get('args'):
            parts.append(' '.join(_render_arg(a) for a in f['args']))
        if f.get('env'):
            for k, v in f['env'].items():
                parts.append('export %s=%s' % (k, v))
        return '\n'.join(parts)

    def repl(m):
        name = m.group(1)
        if name in features:
            return render_feature(features[name], name not in opt_in)
        if name in config_params:
            return str(config_params[name].get('default', ''))
        return m.group(0)

    return re.sub(r'\{\{(\w+)\}\}', repl, content)

# Select the cached weights path that this recipe's vllm serve command should
# resolve `your_model_path` to. The alias file `models/_cache_paths.yaml` maps
# recipe `model.model_id` -> on-runner directory name (no prefix). The
# runner image is the authoritative source for what is actually baked in;
# we try a list of candidate prefix directories and use the first one that
# has the aliased directory on disk. This way a runner image that ships
# weights under `Eco-Tech/` and one that ships them under `models/` (or
# any future layout) both work without changing the alias file.
#
# Without an alias, use the standard org/name cache only if config.json is
# present. Missing weights are skipped, never downloaded or substituted with
# another model. Earlier fallback behavior made the
# runner boot a Qwen tokenizer with GLM flags and produced confusing
# failures (`max_model_len > derived`, `cudagraph_capture_sizes not
# multiples of tp_size`, etc.).
CACHE_BASE = os.environ.get('RECIPE_CACHE_BASE', '/root/.cache/modelscope/hub/models')
CACHE_PREFIXES = ('', 'Eco-Tech', 'vllm-ascend', 'models', '/root/.cache/')   # tried in this order; first hit wins
model_id_for_path = data.get('model', {}).get('model_id', '')
if not model_id_for_path:
    raise ValueError('Recipe must declare model.model_id')
try:
    with open(cache_paths_file, 'r') as f:
        aliases = (yaml.safe_load(f) or {}).get('aliases') or []
except Exception as e:
    print(json.dumps({'action': 'skip', 'reason': f'cache_paths 文件解析失败: {e}'}))
    sys.exit(0)
CACHE_DIR_BY_MODEL = {}
for a in aliases:
    CACHE_DIR_BY_MODEL[a['model_id']] = a['cache_dir']
# Aliases remain authoritative for custom/quantized layouts. New models
# in the standard org/name directory need no separate configuration PR.
aliased = model_id_for_path in CACHE_DIR_BY_MODEL
cache_dir_raw = CACHE_DIR_BY_MODEL.get(model_id_for_path, model_id_for_path)
if isinstance(cache_dir_raw, dict):
    cache_dir = cache_dir_raw.get(hw_key, cache_dir_raw.get('default'))
    if cache_dir is None:
        print(json.dumps({
            'action': 'skip',
            'reason': f'cache_paths 未配置 {hw_key} 的权重目录 (model_id={model_id_for_path})',
        }))
        sys.exit(0)
else:
    cache_dir = cache_dir_raw
CACHE_PATH = None
for prefix in CACHE_PREFIXES:
    candidate = os.path.join(CACHE_BASE, prefix, cache_dir)
    if os.path.isdir(candidate) and (aliased or os.path.isfile(os.path.join(candidate, 'config.json'))):
        CACHE_PATH = candidate
        if prefix != CACHE_PREFIXES[0]:
            print(f"DEBUG: cache resolved under non-default prefix '{prefix}/' for {model_id_for_path}", file=sys.stderr)
        break
if CACHE_PATH is None:
    print(json.dumps({
        'action': 'skip',
        'reason': f'镜像未预装该权重 (model_id={model_id_for_path}, 目录={cache_dir}, 已尝试 {list(CACHE_PREFIXES)})',
    }))
    sys.exit(0)

# Plan and execute the same single-node scenarios; multi-node/PD is handled
# exclusively by verify_multi_node.yaml, even when it shares this recipe.
from verification_plan import single_node_scenarios
scenarios = single_node_scenarios(data, hw_key)
if not scenarios:
    print(json.dumps({'action': 'skip', 'reason': f'No executable single-node scenarios for {hw_key}'}))
    sys.exit(0)
commands = []
for s in scenarios:
    serve_cmd = ''
    verify_cmds = []
    for step in s.get('steps', []):
        content = step.get('content', '')
        verify_cmds.extend(verification_blocks(content))
        blocks = re.findall(r'```(?:bash|shell)\s*\n(.*?)```', content, re.DOTALL)
        serve_blocks = [block for block in blocks if 'vllm serve' in block]
        if not serve_blocks:
            continue
        bash_content = serve_blocks[-1]
        # Remove %%CONFIG:...%% markers (key may contain hyphens)
        bash_content = re.sub(r'%%CONFIG:[^%]+%%', '', bash_content)
        bash_content = re.sub(r'%%/CONFIG:[^%]+%%', '', bash_content)
        # Replace placeholder model paths with actual weights on the runner
        bash_content = bash_content.replace('your_model_path', CACHE_PATH)
        # The cache is offline. Resolve the recipe's literal hub ID too,
        # without accidentally replacing --served-model-name or curl payloads.
        bash_content = re.sub(r'(\bvllm\s+serve\s+)([\'\"]?)' + re.escape(model_id_for_path) + r'\2(?=\s|$)',
                              lambda m: m.group(1) + shlex.quote(CACHE_PATH), bash_content)
        bash_content = re.sub(r'(?m)^(\s*(?:export\s+)?MODEL_PATH=)([\'\"]?)' + re.escape(model_id_for_path) + r'\2(?=\s|$)',
                              lambda m: m.group(1) + shlex.quote(CACHE_PATH), bash_content)
        # Resolve {{name}} config/feature placeholders (mirrors the page)
        bash_content = resolve_placeholders(bash_content, data)
        # Remove speculative-config lines (contain placeholder paths)
        bash_content = re.sub(r'.*--speculative-config.*\n?', '', bash_content)
        if 'vllm serve' in bash_content:
            serve_cmd = bash_content.strip()
    
    if serve_cmd:
        # Local CI verifies the API inside the same container. Replace the
        # documentation placeholders with localhost and the port selected by
        # this scenario's vllm serve command.
        port_match = re.search(r'--port\s+(\d+)', serve_cmd)
        server_port = port_match.group(1) if port_match else '8000'
        verify_cmds = [
            cmd.replace('<node0_ip>', 'localhost')
               .replace('<node_ip>', 'localhost')
               .replace('<server_ip>', 'localhost')
               .replace('<port>', server_port)
            for cmd in verify_cmds + global_verify_cmds
        ]
        
        commands.append({
            'npu': s.get('npu', ''),
            'precision': s.get('precision', ''),
            'deployment': s.get('deployment', ''),
            'case': s.get('case', ''),
            'serve_cmd': serve_cmd,
            'verify_cmds': verify_cmds,
            'benchmark_chat': any('/v1/chat/completions' in cmd for cmd in verify_cmds)
                              and '--runner pooling' not in serve_cmd,
        })

# Find minimum vllm version
min_ver = data.get('model', {}).get('min_vllm_version', '')
model_id = data.get('model', {}).get('model_id', '')

result = {
    'action': 'verify',
    'model_id': model_id,
    'min_vllm_version': min_ver,
    'hw_key': hw_key,
    'pip_setup': pip_content,
    'container_setup': container_content,
    'cache_path': CACHE_PATH,
    'scenarios': commands,
}
print(json.dumps(result))
PYEOF
}

if ! RECIPE_INFO=$(parse_recipe 2>/tmp/recipe-parse-debug.log); then
  log_error "Recipe configuration could not be parsed"
  cat /tmp/recipe-parse-debug.log >&2
  exit 1
fi

ACTION=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('action','skip'))" 2>/dev/null || echo "skip")

if [[ "$ACTION" == "skip" ]]; then
  REASON=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('reason','unknown'))" 2>/dev/null || echo "unknown")
  log_warn "Skipping recipe: $REASON"

  # Write a minimal params.json so publish-status.yml can refresh this
  # recipe's last_nightly_run. Without this, skip recipes (cache miss,
  # hardware unsupported) stay frozen at whatever the last *verify* run
  # produced, which can be misleadingly "pass" or stale "fail".
  # publisher iterates over results/*.params.json — a missing file means
  # the recipe is invisible to the publisher.
  RECIPE_SLUG=$(basename "$RECIPE" .yaml)
  RUN_PARAMS_DIR="${RUN_PARAMS_DIR:-/tmp/verify-results}"
  mkdir -p "$RUN_PARAMS_DIR"
  MODEL_ID=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('model_id',''))" 2>/dev/null || echo "")
  RECIPE_SLUG="$RECIPE_SLUG" MODEL_ID="$MODEL_ID" REASON="$REASON" RECIPE="$RECIPE" RUN_PARAMS_DIR="$RUN_PARAMS_DIR" \
  RECIPE_HW_KEY="$HW_KEY" HEAD_SHA="${HEAD_SHA:-}" TRIGGER_TYPE="${TRIGGER_TYPE:-nightly}" \
  VLLM_ASCEND_IMAGE="${VLLM_ASCEND_IMAGE:-}" STARTED_AT_ISO="${STARTED_AT_ISO:-}" \
  $PYTHON - <<'PYEOF' || log_warn "  Failed to write minimal params.json for skipped recipe"
import sys, json, os
out = os.path.join(os.environ['RUN_PARAMS_DIR'], f"{os.environ['RECIPE_SLUG']}.params.json")
import datetime as _dt
_started_at = os.environ.get('STARTED_AT_ISO', '') or _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
with open(out, 'w') as f:
    json.dump({
        'recipe_path': os.environ['RECIPE'],
        'model_id': os.environ.get('MODEL_ID', ''),
        'hw_key': os.environ.get('RECIPE_HW_KEY', 'atlas_800_a2'),
        'head_sha': os.environ.get('HEAD_SHA', ''),
        'trigger_type': os.environ.get('TRIGGER_TYPE', 'nightly'),
        'image': os.environ.get('VLLM_ASCEND_IMAGE', ''),
        'started_at': _started_at,
        'scenarios': [],
        'skip_reason': os.environ['REASON'],
    }, f, indent=2, ensure_ascii=False)
print(f'[PARAMS] wrote (skip) {out}', file=sys.stderr)
PYEOF

  exit 2
fi

MODEL_ID=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('model_id',''))")
CACHE_PATH=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('cache_path',''))")
export CACHE_PATH
log_info "Model: $MODEL_ID"
log_info "Cached weights: $CACHE_PATH"
log_info "Hardware: $(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('hw_key',''))")"

# Install vllm-ascend
PIP_SETUP=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('pip_setup',''))")
MIN_VERSION=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('min_vllm_version',''))")

if command -v vllm &>/dev/null; then
  log_info "vllm ready: $(vllm --version 2>&1 | head -1 || true)"
else
  log_error "vllm not found in image, cannot proceed"
  exit 1
fi

if [[ -n "$PIP_SETUP" ]]; then
  log_info "Running additional recipe install commands..."
  echo "$PIP_SETUP" | grep -E 'pip\s+install|uv\s+pip\s+install' | while read -r cmd; do
    cmd=$(echo "$cmd" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    log_info "  Running: $cmd"
    eval "$cmd" || log_warn "  Install command returned non-zero (may be non-critical)"
  done || true
fi

# Verify each scenario
SCENARIO_COUNT=$(echo "$RECIPE_INFO" | $PYTHON -c "
import sys,json
print(len(json.loads(sys.stdin.read()).get('scenarios',[])))
")
log_info "Found $SCENARIO_COUNT scenario(s) to verify"

# For smoke test, only verify the first scenario per recipe to save resources
if [[ "${CI_RUNNER_SMOKE:-0}" == "1" ]]; then
  log_info "SMOKE MODE: verifying first scenario only"
fi

echo "$RECIPE_INFO" | $PYTHON -c "
import sys,json,os
info = json.loads(sys.stdin.read())
with open('/tmp/scenario_list.txt', 'w') as flist:
    for i, s in enumerate(info.get('scenarios',[])):
        serve = s.get('serve_cmd','')
        verify = '\n'.join(s.get('verify_cmds',[]))
        with open(f'/tmp/scenario_{i}_serve.sh', 'w') as f:
            f.write(serve)
        with open(f'/tmp/scenario_{i}_verify.sh', 'w') as f:
            f.write(verify)
        flist.write(f'{i}|{s[\"npu\"]}|{s[\"precision\"]}|{s[\"deployment\"]}|{s[\"case\"]}\n')

# === Dump execution params (consumed by publish-status workflow) ===
PARAMS_DIR = os.environ.get('RUN_PARAMS_DIR', '/tmp/verify-results')
HEAD_SHA = os.environ.get('HEAD_SHA', '')
TRIGGER_TYPE = os.environ.get('TRIGGER_TYPE', 'pr')
VLLM_IMAGE = os.environ.get('VLLM_ASCEND_IMAGE', '')
RECIPE_PATH_IN = os.environ.get('RECIPE_YAML_PATH', '')

out_scenarios = []
for i, s in enumerate(info.get('scenarios', [])):
    out_scenarios.append({
        'index': i,
        'npu': s.get('npu', ''),
        'precision': s.get('precision', ''),
        'deployment': s.get('deployment', ''),
        'case': s.get('case', ''),
        'serve_cmd': s.get('serve_cmd', ''),
    })

params_doc = {
    'verification_scope': 'single-node API smoke; not accuracy or full feature coverage',
    'recipe_path': RECIPE_PATH_IN,
    'model_id': info.get('model_id', ''),
    'hw_key': info.get('hw_key', ''),
    'head_sha': HEAD_SHA,
    'trigger_type': TRIGGER_TYPE,
    'image': VLLM_IMAGE,
    'started_at': os.environ.get('STARTED_AT_ISO', '')
    or __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'scenarios': out_scenarios,
}

import os as _os
_os.makedirs(PARAMS_DIR, exist_ok=True)
recipe_slug = _os.path.basename(info.get('_recipe_path', '')).replace('.yaml', '') or 'recipe'
if not recipe_slug or recipe_slug == 'recipe':
    recipe_slug = _os.environ.get('RECIPE_YAML_PATH', 'recipe').split('/')[-1].replace('.yaml', '')
out_path = _os.path.join(PARAMS_DIR, f'{recipe_slug}.params.json')
with open(out_path, 'w') as fp:
    json.dump(params_doc, fp, indent=2, ensure_ascii=False)
print(f'[PARAMS] wrote {out_path}', file=sys.stderr)
"
while IFS='|' read -r idx npu precision deployment case_name; do
  [ -z "$idx" ] && continue

  if [[ "${CI_RUNNER_SMOKE:-0}" == "1" ]] && [[ "$idx" != "0" ]]; then
    continue
  fi

  SERVE_CMD=$(cat "/tmp/scenario_${idx}_serve.sh" 2>/dev/null || echo "")
  VERIFY_CMD=$(cat "/tmp/scenario_${idx}_verify.sh" 2>/dev/null || echo "")

  log_info "--- Scenario [$idx]: $npu / $precision / $deployment / $case_name ---"
  log_info "  Serve command:"
  echo "$SERVE_CMD" | while IFS= read -r line; do log_info "    $line"; done

  if [[ -z "$SERVE_CMD" ]]; then
    log_warn "  No vllm serve command found, skipping"
    SKIPPED=1
    continue
  fi

  if [[ -z "$VERIFY_CMD" ]]; then
    log_warn "  No executable task request in recipe; readiness alone is not a pass"
    SKIPPED=1
    continue
  fi

  # Skip multi-node scenarios (contain positional params like $2, $3)
  if echo "$SERVE_CMD" | grep -qE '\$[0-9]'; then
    log_warn "  Multi-node scenario (contains positional params), skipping"
    SKIPPED=1
    continue
  fi

  VLLM_SCRIPT="/tmp/vllm_serve_${idx}.sh"
  cat > "$VLLM_SCRIPT" <<'SCRIPT_HEREDOC'
#!/usr/bin/env bash
set -eo pipefail
. /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || true
export PATH="/usr/local/bin:/root/.local/bin:$PATH"
SCRIPT_HEREDOC
  cat "/tmp/scenario_${idx}_serve.sh" >> "$VLLM_SCRIPT"
  chmod +x "$VLLM_SCRIPT"

  log_info "  Starting vllm serve..."
  bash "$VLLM_SCRIPT" &
  SERVE_PID=$!

  # Recipes may use a non-default API port. Keep readiness checks and
  # benchmark configuration aligned with the port passed to vllm serve.
  SERVER_PORT=$(printf '%s\n' "$SERVE_CMD" | sed -nE 's/.*--port[[:space:]]+([0-9]+).*/\1/p' | tail -1)
  SERVER_PORT=${SERVER_PORT:-8000}

  # Wait for /v1/models to become ready
  log_info "  Waiting for server ready on port ${SERVER_PORT}..."
  READY=0
  for i in $(seq 1 300); do
    if curl -sf "http://localhost:${SERVER_PORT}/v1/models" > /dev/null 2>&1; then
      READY=1
      log_info "  Server ready after ${i}s"
      break
    fi
    if [[ $((i % 30)) -eq 0 ]]; then
      log_info "  Still waiting... (${i}s elapsed)"
      npu-smi info || true
    fi
    sleep 2
  done

  if [[ "$READY" -eq 0 ]]; then
    log_error "  Server failed to become ready within 600s"
    kill $SERVE_PID 2>/dev/null || true
    STATUS=1
    continue
  fi

  # Verify /v1/models returns expected content
  MODELS_RESP=$(curl -sf "http://localhost:${SERVER_PORT}/v1/models")
  if echo "$MODELS_RESP" | grep -qi "model"; then
    log_info "  /v1/models OK"
  else
    log_error "  /v1/models returned unexpected response"
    kill $SERVE_PID 2>/dev/null || true
    STATUS=1
    continue
  fi

  # Run recipe curl verification commands
  TASK_VERIFIED=0
  if [[ -n "$VERIFY_CMD" ]]; then
    log_info "  Running recipe verification commands..."
    # Write curl command to temp script and execute
    CURL_SCRIPT="/tmp/curl_verify_${idx}.sh"
    echo "#!/usr/bin/env bash" > "$CURL_SCRIPT"
    echo "set -eo pipefail" >> "$CURL_SCRIPT"
    PROOF_FILE="${RUN_PARAMS_DIR:-/tmp/verify-results}/$(basename "$RECIPE" .yaml)-${idx}.requests.jsonl"
    mkdir -p "$(dirname "$PROOF_FILE")"
    : > "$PROOF_FILE"
    printf 'export PYTHON=%q\n' "$PYTHON" >> "$CURL_SCRIPT"
    printf 'export RECIPE_CHECKS_SCRIPT=%q\n' "$SCRIPT_DIR/../.github/_scripts/recipe_checks.py" >> "$CURL_SCRIPT"
    printf 'export CI_REQUEST_PROOF_FILE=%q\n' "$PROOF_FILE" >> "$CURL_SCRIPT"
    printf 'export CI_SERVER_PORT=%q\n' "$SERVER_PORT" >> "$CURL_SCRIPT"
    printf 'export PYTHONPATH=%q\n' "$SCRIPT_DIR/../.github/_scripts:${PYTHONPATH:-}" >> "$CURL_SCRIPT"
    "$PYTHON" "$SCRIPT_DIR/../.github/_scripts/recipe_checks.py" --shell-wrapper >> "$CURL_SCRIPT"
    echo "$VERIFY_CMD" >> "$CURL_SCRIPT"
    chmod +x "$CURL_SCRIPT"
    if ! RESP=$(bash "$CURL_SCRIPT" 2>&1); then
      log_error "  Recipe curl verification FAILED"
      log_error "  ====== CURL FAILURE OUTPUT ======"
      echo "$RESP" | while IFS= read -r rline; do log_error "  | $rline"; done
      log_error "  ====== END ======"
      STATUS=1
    elif "$PYTHON" "$SCRIPT_DIR/../.github/_scripts/recipe_checks.py" --check-proof "$PROOF_FILE"; then
      TASK_VERIFIED=1
      log_info "  Recipe curl verification PASSED"
      log_info "  ====== CURL RESPONSE ======"
      echo "$RESP" | while IFS= read -r rline; do log_info "  | $rline"; done
      log_info "  ====== END ======"
    else
      proof_rc=$?
      if [[ "$proof_rc" -eq 2 ]]; then
        log_warn "  No supported task request was verified; marking this recipe skipped"
        SKIPPED=1
      else
        log_error "  Task response validation FAILED"
        STATUS=1
      fi
    fi
  fi

  # Run aisbench performance evaluation (Section 8 of tutorial)
  BENCHMARK_CHAT=$(echo "$RECIPE_INFO" | $PYTHON -c 'import sys,json; print(int(json.load(sys.stdin)["scenarios"][int(sys.argv[1])].get("benchmark_chat", False)))' "$idx")
  if [[ "$TASK_VERIFIED" -eq 1 && "$BENCHMARK_CHAT" -eq 1 ]] && command -v ais_bench &>/dev/null; then
    log_info "  Running aisbench performance evaluation..."
    # Patch the installed model config to set model path and port
    AIS_CFG="/usr/local/python3.12.13/lib/python3.12/site-packages/ais_bench/benchmark/configs/models/vllm_api/vllm_api_stream_chat.py"
    if [[ -f "$AIS_CFG" ]]; then
      sed -i "s|path=\".*\"|path=\"${CACHE_PATH}\"|" "$AIS_CFG"
      sed -E -i "s|host_port[[:space:]]*=[[:space:]]*[0-9]+|host_port=${SERVER_PORT}|" "$AIS_CFG"
      log_info "  Patched ais_bench model config (path + port)"
    fi
    BENCH_OUTPUT=$(ais_bench --models vllm_api_stream_chat --datasets synthetic_gen --mode perf --debug --num-prompts 50 2>&1 || echo "AISBENCH_FAILED")
    echo "$BENCH_OUTPUT" | tail -30
    BENCH_FILE="/tmp/verify-results/$(basename "$RECIPE" .yaml).bench"
    echo "===== AISBENCH PERFORMANCE =====" > "$BENCH_FILE"
    echo "$BENCH_OUTPUT" >> "$BENCH_FILE"
    echo "===== END =====" >> "$BENCH_FILE"
    log_info "  Benchmark saved to $BENCH_FILE"
  else
    log_warn "  Chat benchmark unavailable or not applicable to this task; skipping benchmark"
    echo "benchmark_skipped" > "/tmp/verify-results/$(basename "$RECIPE" .yaml).bench"
  fi

  # Kill the server and all children
  log_info "  Stopping vllm serve..."
  if [[ -n "$SERVE_PID" ]] && kill -0 $SERVE_PID 2>/dev/null; then
    kill -TERM -- -$SERVE_PID 2>/dev/null || kill $SERVE_PID 2>/dev/null || true
    sleep 2
    kill -KILL -- -$SERVE_PID 2>/dev/null || kill -9 $SERVE_PID 2>/dev/null || true
    timeout 30 wait $SERVE_PID 2>/dev/null || true
  fi
  # Force cleanup any remaining vllm processes
  pkill -f "vllm serve" 2>/dev/null || true
  log_info "  Server stopped."

  if [[ "$STATUS" -ne 0 ]]; then
    log_error "FAILED: $MODEL_ID scenario [$idx]"
  else
    log_info "PASSED: $MODEL_ID scenario [$idx]"
  fi
done < /tmp/scenario_list.txt

# Exit code precedence (matters for the wrapper's .status write):
#   1 = fail    — at least one scenario actually failed; this MUST win over skip
#                 so a recipe with mixed fail+skip scenarios doesn't show as gray
#   2 = skip   — every scenario was auto-skipped (e.g. multi-node on single-node runner)
#   0 = pass   — every scenario verified
if [[ "$STATUS" -gt 0 ]]; then
  exit 1
fi
if [[ "$SKIPPED" -gt 0 ]]; then
  exit 2
fi

exit 0
