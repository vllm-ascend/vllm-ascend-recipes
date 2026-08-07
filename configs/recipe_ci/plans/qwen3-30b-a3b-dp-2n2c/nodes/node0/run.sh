#!/usr/bin/env bash
set -euo pipefail

export OMP_PROC_BIND=false
export OMP_NUM_THREADS=10
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export HCCL_BUFFSIZE=1024
export VLLM_USE_MODELSCOPE=true

exec vllm serve "$RECIPE_MODEL_PATH" \
    --host "$RECIPE_LOCAL_IP" \
    --port "$RECIPE_SERVICE_PORT_START" \
    --served-model-name "$RECIPE_SERVED_MODEL_NAME" \
    --data-parallel-size 4 \
    --data-parallel-size-local 2 \
    --data-parallel-address "$RECIPE_NODE_0_IP" \
    --data-parallel-rpc-port 12321 \
    --tensor-parallel-size 1 \
    --max-model-len 4096 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 4096 \
    --gpu-memory-utilization 0.9 \
    --trust-remote-code \
    --enable-expert-parallel \
    --no-enable-prefix-caching \
    --enforce-eager
