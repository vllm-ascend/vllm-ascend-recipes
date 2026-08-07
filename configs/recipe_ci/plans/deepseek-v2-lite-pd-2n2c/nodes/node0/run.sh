#!/usr/bin/env bash
set -euo pipefail

exec python3 "$RECIPE_REPOSITORY_ROOT/scripts/recipe_ci/run_online_dp.py" \
    "$RECIPE_VLLM_ASCEND_ROOT/examples/external_online_dp/launch_online_dp.py" \
    --dp-size 2 \
    --tp-size 1 \
    --dp-size-local "$RECIPE_SERVICE_COUNT" \
    --dp-rank-start 0 \
    --dp-address "$RECIPE_LOCAL_IP" \
    --dp-rpc-port 12321 \
    --vllm-start-port "$RECIPE_SERVICE_PORT_START"
