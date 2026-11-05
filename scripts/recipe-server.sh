#!/usr/bin/env bash
# Shared single-node server lifecycle. Source from verify-recipe.sh.

SERVE_PID=''
SERVER_OWNER_TOKEN=''
SERVER_FAILURE_REASON=''

configure_recipe_server() {
  export VLLM_ENGINE_READY_TIMEOUT_S="${VLLM_ENGINE_READY_TIMEOUT_S:-1800}"
  export RECIPE_SERVER_READY_TIMEOUT_S="${RECIPE_SERVER_READY_TIMEOUT_S:-2400}"
  local name value
  for name in VLLM_ENGINE_READY_TIMEOUT_S RECIPE_SERVER_READY_TIMEOUT_S; do
    value=${!name}
    if [[ ! "$value" =~ ^[1-9][0-9]{0,4}$ ]] || (( value > 86400 )); then
      printf '[ERROR] %s must be an integer between 1 and 86400 seconds\n' "$name" >&2
      return 1
    fi
  done
  if (( RECIPE_SERVER_READY_TIMEOUT_S <= VLLM_ENGINE_READY_TIMEOUT_S )); then
    printf '[ERROR] CI readiness timeout must exceed VLLM_ENGINE_READY_TIMEOUT_S\n' >&2
    return 1
  fi
  command -v setsid >/dev/null && command -v timeout >/dev/null
}

start_recipe_server() {
  local launch_signal=0 term_trap int_trap
  term_trap=$(trap -p TERM)
  int_trap=$(trap -p INT)
  trap 'launch_signal=143' TERM
  trap 'launch_signal=130' INT
  # Noninteractive Bash has job control disabled: setsid's PID is the new PGID.
  # All vLLM workers inherit this group, including after the shell exits.
  SERVER_OWNER_TOKEN="${BASHPID}-${RANDOM}-${RANDOM}"
  RECIPE_SERVER_OWNER_TOKEN="$SERVER_OWNER_TOKEN" setsid bash "$1" &
  SERVE_PID=$!
  trap - TERM INT
  eval "$term_trap"
  eval "$int_trap"
  if (( launch_signal )); then exit "$launch_signal"; fi
}

signal_recipe_descendants() {
  # Detached workers keep the launch token even if their parent/group exits.
  # Read only the token; never print process environments or match by name.
  timeout --signal=KILL 2 "${PYTHON:-python3}" - "$SERVER_OWNER_TOKEN" "$1" <<'PYEOF' || true
import os, signal, sys
from pathlib import Path
token = b'RECIPE_SERVER_OWNER_TOKEN=' + sys.argv[1].encode()
signum = getattr(signal, 'SIG' + sys.argv[2])
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        if token in (entry / 'environ').read_bytes().split(b'\0'):
            os.kill(int(entry.name), signum)
    except OSError:
        pass
PYEOF
}

stop_recipe_server() {
  local pid=${SERVE_PID:-} cleanup_signal=0 term_trap int_trap
  [[ -n "$pid" ]] || return 0
  term_trap=$(trap -p TERM)
  int_trap=$(trap -p INT)
  # Finish escalation before honoring a signal received during EXIT cleanup.
  trap 'cleanup_signal=143' TERM
  trap 'cleanup_signal=130' INT
  signal_recipe_descendants TERM
  # Signal the group even if its original leader has already exited.
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  sleep 2 || true
  signal_recipe_descendants KILL
  kill -KILL -- "-$pid" 2>/dev/null || true
  # A device worker in uninterruptible I/O may survive SIGKILL; never wait
  # indefinitely for it. Bash reaps exited background jobs automatically.
  if ! kill -0 "$pid" 2>/dev/null; then wait "$pid" 2>/dev/null || true; fi
  SERVE_PID=''
  SERVER_OWNER_TOKEN=''
  trap - TERM INT
  eval "$term_trap"
  eval "$int_trap"
  if (( cleanup_signal )); then exit "$cleanup_signal"; fi
}

wait_recipe_server() {
  local port=$1 started=$SECONDS now remaining budget delay exit_code
  local deadline=$((SECONDS + RECIPE_SERVER_READY_TIMEOUT_S))
  local next_monitor=$((SECONDS + 30))
  SERVER_FAILURE_REASON=''
  printf '[INFO] Waiting for server on port %s (CI timeout %ss; engine timeout %ss)\n' \
    "$port" "$RECIPE_SERVER_READY_TIMEOUT_S" "$VLLM_ENGINE_READY_TIMEOUT_S"
  while (( SECONDS < deadline )); do
    if ! kill -0 "$SERVE_PID" 2>/dev/null; then
      if wait "$SERVE_PID"; then exit_code=0; else exit_code=$?; fi
      SERVER_FAILURE_REASON="Model server exited before readiness (exit code $exit_code)"
      printf '[ERROR] %s\n' "$SERVER_FAILURE_REASON" >&2
      return 1
    fi
    now=$SECONDS
    if (( now >= next_monitor )); then
      budget=$((deadline - now))
      (( budget > 0 )) || break
      printf '[INFO] Still waiting... (%ss elapsed)\n' "$((now - started))"
      (( budget > 5 )) && budget=5
      timeout --signal=KILL "$budget" npu-smi info || true
      next_monitor=$((started + ((SECONDS - started) / 30 + 1) * 30))
    fi
    remaining=$((deadline - SECONDS))
    (( remaining > 0 )) || break
    budget=$remaining
    (( budget > 5 )) && budget=5
    (( next_monitor - SECONDS < budget )) && budget=$((next_monitor - SECONDS))
    (( budget > 0 )) || continue
    if curl --noproxy '*' --connect-timeout "$budget" --max-time "$budget" \
      -sf "http://localhost:${port}/v1/models" >/dev/null 2>&1; then
      printf '[INFO] Server ready after %ss\n' "$((SECONDS - started))"
      return 0
    fi
    remaining=$((deadline - SECONDS))
    (( remaining > 0 )) || break
    delay=2
    (( remaining < delay )) && delay=$remaining
    (( next_monitor - SECONDS < delay )) && delay=$((next_monitor - SECONDS))
    (( delay > 0 )) && sleep "$delay"
  done
  SERVER_FAILURE_REASON="Model server did not become ready within ${RECIPE_SERVER_READY_TIMEOUT_S}s"
  printf '[ERROR] Server failed to become ready within %ss (%ss elapsed)\n' \
    "$RECIPE_SERVER_READY_TIMEOUT_S" "$((SECONDS - started))" >&2
  return 1
}
