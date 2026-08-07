#!/usr/bin/env bash
set -euo pipefail

logical_devices=$1
service_port=$2
dp_size=$3
dp_rank=$4
dp_address=$5
dp_rpc_port=$6
tp_size=$7

# launch_online_dp.py passes logical indexes such as 0,1,...,7. Map them to the
# physical cards selected by the CI job through ASCEND_RT_VISIBLE_DEVICES.
IFS=',' read -r -a available_devices <<< "${RECIPE_CI_VISIBLE_DEVICES:-${ASCEND_RT_VISIBLE_DEVICES:?set ASCEND_RT_VISIBLE_DEVICES}}"
IFS=',' read -r -a logical_indexes <<< "$logical_devices"
selected_devices=()
for index in "${logical_indexes[@]}"; do
    selected_devices+=("${available_devices[$index]}")
done
ASCEND_RT_VISIBLE_DEVICES=$(IFS=,; echo "${selected_devices[*]}")
export ASCEND_RT_VISIBLE_DEVICES

export VLLM_RPC_TIMEOUT=3600000
export VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=30000
export HCCL_EXEC_TIMEOUT=204
export HCCL_CONNECT_TIMEOUT=1200
export OMP_PROC_BIND=false
export OMP_NUM_THREADS=10
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export HCCL_BUFFSIZE=1024
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_FLASHCOMM1=1
export HCCL_OP_EXPANSION_MODE=AIV
export LD_PRELOAD="/usr/lib/aarch64-linux-gnu/libjemalloc.so.2:${LD_PRELOAD:-}"

kv_transfer_config='{
  "kv_connector": "MooncakeHybridConnector",
  "kv_role": "kv_producer",
  "kv_port": "30000",
  "engine_id": "0",
  "kv_connector_extra_config": {
    "prefill": {"dp_size": 8, "tp_size": 1},
    "decode": {"dp_size": 8, "tp_size": 1}
  }
}'

exec vllm serve "$RECIPE_MODEL_PATH" \
    --host 0.0.0.0 \
    --port "$service_port" \
    --data-parallel-size "$dp_size" \
    --data-parallel-rank "$dp_rank" \
    --data-parallel-address "$dp_address" \
    --data-parallel-rpc-port "$dp_rpc_port" \
    --tensor-parallel-size "$tp_size" \
    --enable-expert-parallel \
    --seed 1024 \
    --served-model-name "$RECIPE_SERVED_MODEL_NAME" \
    --max-model-len 135000 \
    --max-num-batched-tokens 4096 \
    --max-num-seqs 16 \
    --no-disable-hybrid-kv-cache-manager \
    --model-loader-extra-config '{"enable_multithread_load":"true","num_threads":128}' \
    --async-scheduling \
    --enable-prefix-caching \
    --safetensors-load-strategy prefetch \
    --speculative-config '{"num_speculative_tokens":1,"method":"mtp","enforce_eager":true}' \
    --trust-remote-code \
    --block-size 128 \
    --tokenizer-mode deepseek_v4 \
    --tool-call-parser deepseek_v4 \
    --enable-auto-tool-choice \
    --reasoning-parser deepseek_v4 \
    --gpu-memory-utilization 0.9 \
    --quantization ascend \
    --enforce-eager \
    --additional-config '{"enable_cpu_binding":true,"enable_shared_expert_dp":true}' \
    --kv-transfer-config "$kv_transfer_config"
