#!/usr/bin/env bash
# format.sh — run the same checks CI runs, locally.
#
# Usage:
#   ./scripts/format.sh          # check only (mirrors CI, exits non-zero on failure)
#   ./scripts/format.sh --fix    # auto-fix what's fixable (eslint --fix + prettier --write), then re-check
#
# Mirrors .github/workflows/lint.yml step-by-step:
#   1. validate model YAML   (pnpm validate)
#   2. type check             (pnpm typecheck)
#   3. ESLint                 (pnpm lint)
#   4. Prettier format check  (pnpm format:check)
set -euo pipefail

# Always run from the repo root, regardless of where the script is invoked from.
cd "$(dirname "$0")/.."

FIX=0
if [[ "${1:-}" == "--fix" ]]; then
  FIX=1
fi

# --- color helpers (disabled when not a TTY, so CI logs stay clean) ---
if [[ -t 1 ]]; then
  GREEN=$'\033[0;32m'
  RED=$'\033[0;31m'
  YELLOW=$'\033[0;33m'
  BOLD=$'\033[1m'
  RESET=$'\033[0m'
else
  GREEN="" RED="" YELLOW="" BOLD="" RESET=""
fi

passed=0
failed=0

run_step() {
  local name="$1"
  local cmd="$2"
  echo ""
  echo "${BOLD}▶ ${name}${RESET}"
  echo "    $ ${cmd}"
  if eval "$cmd"; then
    echo "${GREEN}  ✓ ${name} passed${RESET}"
    passed=$((passed + 1))
  else
    echo "${RED}  ✗ ${name} FAILED${RESET}"
    failed=$((failed + 1))
  fi
}

# --- optional auto-fix pass ---
if [[ "$FIX" -eq 1 ]]; then
  echo "${BOLD}Auto-fixing (Prettier --write + ESLint --fix)...${RESET}"
  pnpm format
  pnpm lint:fix
fi

# --- mirror CI exactly: same 5 steps, same commands ---
run_step "Validate model YAML" "pnpm validate"
run_step "Type check"          "pnpm typecheck"
run_step "ESLint"              "pnpm lint"
run_step "Prettier check"      "pnpm format:check"

# --- summary ---
echo ""
echo "${BOLD}────────────────────────────────${RESET}"
if [[ "$failed" -eq 0 ]]; then
  echo "${GREEN}${BOLD}All checks passed (${passed} steps).${RESET}"
  echo "${GREEN}This matches what CI will run.${RESET}"
  exit 0
else
  echo "${RED}${BOLD}${failed} step(s) failed, ${passed} passed.${RESET}"
  echo "${YELLOW}Tip: run ${BOLD}./scripts/format.sh --fix${RESET} to auto-fix formatting and lint issues.${RESET}"
  exit 1
fi
