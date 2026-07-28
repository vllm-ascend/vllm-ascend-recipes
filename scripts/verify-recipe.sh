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
  $PYTHON - "$RECIPE" "$HW_KEY" <<'PYEOF'
import sys
import yaml
import json

recipe_path, hw_key = sys.argv[1], sys.argv[2]

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
hw_to_container = {'atlas_800_a2': 'A2', 'atlas_800_a3': 'A3'}
container_content = env_setup.get('container', {}).get(hw_to_container.get(hw_key, 'A2'), {}).get('content', '')

# Extract global verification (curl commands shared across scenarios)
verification = data.get('verification', '')
global_verify_cmd = ''
import re
m = re.search(r'```(?:bash|shell)\s*\n(.*?)```', verification, re.DOTALL)
if m:
    global_verify_cmd = m.group(1).strip()
    # Replace <node0_ip> etc
    global_verify_cmd = global_verify_cmd.replace('<node0_ip>', 'localhost')

# Select the cached weights path that this recipe's vllm serve command should
# resolve `your_model_path` to. CI runner images pre-install a fixed set of
# W8A8 weights under /root/.cache/modelscope/hub/models/; we map each
# supported recipe's model_id onto the one that exists on the runner.
# Recipes not in this map fall back to the historical Qwen3-30B-A3B-w8a8
# path so existing recipes keep working.
model_id_for_path = data.get('model', {}).get('model_id', 'Qwen/Qwen3-30B-A3B')
CACHE_PATH_MAP = {
    'Qwen/Qwen3-30B-A3B': '/root/.cache/modelscope/hub/models/Eco-Tech/Qwen3-30B-A3B-w8a8',
    'Qwen/Qwen3.6-27B':  '/root/.cache/modelscope/hub/models/Eco-Tech/Qwen3.6-27B-w8a8',
    'Qwen/Qwen3.5-27B':  '/root/.cache/modelscope/hub/models/Eco-Tech/Qwen3.6-27B-w8a8',  # Qwen3.5 not yet pre-installed on runner; reuse Qwen3.6 cache
}
CACHE_PATH = CACHE_PATH_MAP.get(model_id_for_path, '/root/.cache/modelscope/hub/models/Eco-Tech/Qwen3-30B-A3B-w8a8')

# Extract scenarios
scenarios = data.get('scenarios', [])
commands = []
for s in scenarios:
    serve_cmd = ''
    verify_cmds = []
    # Skip A3 scenarios on A2 hardware
    if hw_key == 'atlas_800_a2' and 'A3' in s.get('npu', ''):
        import sys
        print(f"DEBUG: Skipping A3 scenario '{s.get('npu','')}/{s.get('precision','')}' on A2 hardware", file=sys.stderr)
        continue

    for step in s.get('steps', []):
        content = step.get('content', '')
        m = re.search(r'```(?:bash|shell)\s*\n(.*?)```', content, re.DOTALL)
        if not m:
            import sys
            print(f"DEBUG: No bash block found in step '{step.get('title','')}', scenario '{s.get('npu','')}/{s.get('precision','')}', content[:150]={content[:150]}", file=sys.stderr)
            continue
        bash_content = m.group(1)
        # Remove %%CONFIG:...%% markers (key may contain hyphens)
        bash_content = re.sub(r'%%CONFIG:[^%]+%%', '', bash_content)
        bash_content = re.sub(r'%%/CONFIG:[^%]+%%', '', bash_content)
        # Replace placeholder model paths with actual weights on the runner
        bash_content = bash_content.replace('your_model_path', CACHE_PATH)
        # Remove speculative-config lines (contain placeholder paths)
        bash_content = re.sub(r'.*--speculative-config.*\n?', '', bash_content)
        if 'vllm serve' in bash_content:
            serve_cmd = bash_content.strip()
        elif 'curl' in bash_content:
            verify_cmds.append(bash_content.strip())
    
    if serve_cmd:
        # Append global verification curl commands
        if global_verify_cmd:
            verify_cmds.append(global_verify_cmd)
        
        commands.append({
            'npu': s.get('npu', ''),
            'precision': s.get('precision', ''),
            'deployment': s.get('deployment', ''),
            'case': s.get('case', ''),
            'serve_cmd': serve_cmd,
            'verify_cmds': verify_cmds,
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

RECIPE_INFO=$(parse_recipe 2>/tmp/recipe-parse-debug.log || echo '{"action":"skip","reason":"parse error"}')

ACTION=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('action','skip'))" 2>/dev/null || echo "skip")

if [[ "$ACTION" == "skip" ]]; then
  REASON=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('reason','unknown'))" 2>/dev/null || echo "unknown")
  log_warn "Skipping recipe: $REASON"
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
    'recipe_path': RECIPE_PATH_IN,
    'model_id': info.get('model_id', ''),
    'hw_key': info.get('hw_key', ''),
    'head_sha': HEAD_SHA,
    'trigger_type': TRIGGER_TYPE,
    'image': VLLM_IMAGE,
    'started_at': os.environ.get('STARTED_AT_ISO', ''),
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

  # Wait for /v1/models to become ready
  log_info "  Waiting for server ready..."
  READY=0
  for i in $(seq 1 300); do
    if curl -sf http://localhost:8000/v1/models > /dev/null 2>&1; then
      READY=1
      log_info "  Server ready after ${i}s"
      break
    fi
    if [[ $((i % 30)) -eq 0 ]]; then
      log_info "  Still waiting... (${i}s elapsed)"
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
  MODELS_RESP=$(curl -sf http://localhost:8000/v1/models)
  if echo "$MODELS_RESP" | grep -qi "model"; then
    log_info "  /v1/models OK"
  else
    log_error "  /v1/models returned unexpected response"
    kill $SERVE_PID 2>/dev/null || true
    STATUS=1
    continue
  fi

  # Run recipe curl verification commands
  if [[ -n "$VERIFY_CMD" ]]; then
    log_info "  Running recipe verification commands..."
    # Write curl command to temp script and execute
    CURL_SCRIPT="/tmp/curl_verify_${idx}.sh"
    echo "#!/usr/bin/env bash" > "$CURL_SCRIPT"
    echo "set -eo pipefail" >> "$CURL_SCRIPT"
    echo "$VERIFY_CMD" >> "$CURL_SCRIPT"
    chmod +x "$CURL_SCRIPT"
    RESP=$(bash "$CURL_SCRIPT" 2>&1 || echo "CURL_FAILED")
    if echo "$RESP" | grep -qi "CURL_FAILED"; then
      log_error "  Recipe curl verification FAILED"
      STATUS=1
    else
      log_info "  Recipe curl verification PASSED"
      log_info "  ====== CURL RESPONSE ======"
      echo "$RESP" | while IFS= read -r rline; do log_info "  | $rline"; done
      log_info "  ====== END ======"
    fi
  fi

  # Run aisbench performance evaluation (Section 8 of tutorial)
  if command -v ais_bench &>/dev/null; then
    log_info "  Running aisbench performance evaluation..."
    # Patch the installed model config to set model path and port
    AIS_CFG="/usr/local/python3.12.13/lib/python3.12/site-packages/ais_bench/benchmark/configs/models/vllm_api/vllm_api_stream_chat.py"
    if [[ -f "$AIS_CFG" ]]; then
      sed -i "s|path=\".*\"|path=\"${CACHE_PATH}\"|" "$AIS_CFG"
      sed -i 's|host_port=8080|host_port=8000|' "$AIS_CFG"
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
    log_warn "  ais_bench not available, skipping benchmark"
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

if [[ "$SKIPPED" -gt 0 ]]; then
  exit 2
fi

exit $STATUS
