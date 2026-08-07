#!/usr/bin/env bash
set -euo pipefail

# The upstream A3 topology is DP8 x TP2. This A2 CI plan keeps TP2 and scales
# local DP down to four ranks so one Prefill node consumes eight NPUs.
exec python3 "$RECIPE_REPOSITORY_ROOT/scripts/recipe_ci/run_online_dp.py" \
    "$RECIPE_VLLM_ASCEND_ROOT/examples/external_online_dp/launch_online_dp.py" \
    --dp-size 4 \
    --tp-size 2 \
    --dp-size-local 4 \
    --dp-rank-start 0 \
    --dp-address "$RECIPE_LOCAL_IP" \
    --dp-rpc-port 12321 \
    --vllm-start-port "$RECIPE_SERVICE_PORT_START"
