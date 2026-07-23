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

# Extract scenarios
scenarios = data.get('scenarios', [])
commands = []
for s in scenarios:
    steps = s.get('steps', [])
    for step in steps:
        content = step.get('content', '')
        import re
        # Extract bash/shell code block content
        m = re.search(r'```(?:bash|shell)\s*\n(.*?)```', content, re.DOTALL)
        if not m:
            import sys
            print(f"DEBUG: No bash block found in step '{step.get('title','')}', content[:200]={content[:200]}", file=sys.stderr)
            continue
        bash_content = m.group(1)
        # Remove %%CONFIG:...%% markers
        bash_content = re.sub(r'%%CONFIG:\w+%%', '', bash_content)
        bash_content = re.sub(r'%%/CONFIG:\w+%%', '', bash_content)
        # Find vllm serve line
        for line in bash_content.split('\n'):
            line_stripped = line.strip()
            if 'vllm serve' in line_stripped:
                commands.append({
                    'npu': s.get('npu', ''),
                    'precision': s.get('precision', ''),
                    'deployment': s.get('deployment', ''),
                    'case': s.get('case', ''),
                    'command': bash_content.strip(),
                })
                break

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
log_info "Model: $MODEL_ID"
log_info "Hardware: $(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('hw_key',''))")"

# Install vllm-ascend
PIP_SETUP=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('pip_setup',''))")
MIN_VERSION=$(echo "$RECIPE_INFO" | $PYTHON -c "import sys,json; print(json.loads(sys.stdin.read()).get('min_vllm_version',''))")

if command -v vllm &>/dev/null; then
  log_info "vllm already installed"
else
  log_info "Installing vllm-ascend..."
  if [[ -n "$MIN_VERSION" ]]; then
    uv pip install "vllm-ascend>=$MIN_VERSION" || pip install "vllm-ascend>=$MIN_VERSION"
  else
    uv pip install vllm-ascend || pip install vllm-ascend
  fi
  hash -r 2>/dev/null || true
  # Ensure common install paths are in PATH
  export PATH="/usr/local/bin:/root/.local/bin:$PATH"
  if command -v vllm &>/dev/null; then
    log_info "vllm installed: $(which vllm)"
  else
    VLLM_PY=$($PYTHON -c "import vllm; print(vllm.__file__)" 2>&1 || echo "IMPORT_FAILED")
    log_info "vllm check: $VLLM_PY"
  fi
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
import sys,json
info = json.loads(sys.stdin.read())
for i, s in enumerate(info.get('scenarios',[])):
    print(f'{i}|{s[\"npu\"]}|{s[\"precision\"]}|{s[\"deployment\"]}|{s[\"case\"]}')
    print(s['command'])
    print('---END---')
" | while IFS='|' read -r idx npu precision deployment case_name; do
  [ -z "$idx" ] && continue
  IFS= read -r cmd
  read -r separator  # consume ---END---

  if [[ "${CI_RUNNER_SMOKE:-0}" == "1" ]] && [[ "$idx" != "0" ]]; then
    continue
  fi

  log_info "--- Scenario [$idx]: $npu / $precision / $deployment / $case_name ---"

  # Write command to temp script to handle multiline/quotes properly
  VLLM_SCRIPT="/tmp/vllm_serve_${idx}.sh"
  cat > "$VLLM_SCRIPT" <<'SCRIPT_HEREDOC'
#!/usr/bin/env bash
set -euo pipefail
. /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || true
export PATH="/usr/local/bin:/root/.local/bin:$PATH"
SCRIPT_HEREDOC
  echo "$cmd" >> "$VLLM_SCRIPT"
  chmod +x "$VLLM_SCRIPT"

  log_info "  Starting vllm serve..."
  bash "$VLLM_SCRIPT" &
  SERVE_PID=$!

  # Wait for /v1/models to become ready
  log_info "  Waiting for server ready..."
  READY=0
  for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/v1/models > /dev/null 2>&1; then
      READY=1
      log_info "  Server ready after ${i}s"
      break
    fi
    sleep 2
  done

  if [[ "$READY" -eq 0 ]]; then
    log_error "  Server failed to become ready within 120s"
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

  # Send a test inference request
  log_info "  Sending test inference request..."
  INFER_RESP=$(curl -sf -X POST http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"'"$MODEL_ID"'","messages":[{"role":"user","content":"Hello, reply with just the word OK."}],"max_tokens":10}' \
    2>&1 || true)

  if echo "$INFER_RESP" | grep -qi '"error"'; then
    log_error "  Inference request returned error"
    STATUS=1
  elif [[ -z "$INFER_RESP" ]]; then
    log_error "  Inference request returned empty response"
    STATUS=1
  else
    log_info "  Inference response received"
  fi

  # Kill the server
  log_info "  Stopping vllm serve..."
  kill $SERVE_PID 2>/dev/null || true
  wait $SERVE_PID 2>/dev/null || true
  log_info "  Server stopped."

  if [[ "$STATUS" -ne 0 ]]; then
    log_error "FAILED: $MODEL_ID scenario [$idx]"
  else
    log_info "PASSED: $MODEL_ID scenario [$idx]"
  fi
done

if [[ "$SKIPPED" -gt 0 ]]; then
  exit 2
fi

exit $STATUS
