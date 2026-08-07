#!/usr/bin/env bash
set -euo pipefail

# Backend lists are an explicit Recipe conversion product. Runner does not infer
# Prefill/Decode topology from node roles.
exec python3 \
    "$RECIPE_VLLM_ASCEND_ROOT/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py" \
    --host "$RECIPE_LOCAL_IP" \
    --port "$RECIPE_GATEWAY_PORT" \
    --prefiller-hosts \
    "$RECIPE_NODE_0_IP" "$RECIPE_NODE_0_IP" \
    "$RECIPE_NODE_0_IP" "$RECIPE_NODE_0_IP" \
    "$RECIPE_NODE_0_IP" "$RECIPE_NODE_0_IP" \
    "$RECIPE_NODE_0_IP" "$RECIPE_NODE_0_IP" \
    --prefiller-ports 7100 7101 7102 7103 7104 7105 7106 7107 \
    --decoder-hosts \
    "$RECIPE_NODE_1_IP" "$RECIPE_NODE_1_IP" \
    "$RECIPE_NODE_1_IP" "$RECIPE_NODE_1_IP" \
    "$RECIPE_NODE_1_IP" "$RECIPE_NODE_1_IP" \
    "$RECIPE_NODE_1_IP" "$RECIPE_NODE_1_IP" \
    --decoder-ports \
    7100 7101 7102 7103 7104 7105 7106 7107
