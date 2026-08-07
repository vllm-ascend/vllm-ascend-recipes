#!/usr/bin/env bash
set -euo pipefail

logical_devices=$1
service_port=$2
dp_size=$3
dp_rank=$4
dp_address=$5
dp_rpc_port=$6
tp_size=$7

IFS=',' read -r -a available_devices <<< "${RECIPE_CI_VISIBLE_DEVICES:-${ASCEND_RT_VISIBLE_DEVICES:?set ASCEND_RT_VISIBLE_DEVICES}}"
IFS=',' read -r -a logical_indexes <<< "$logical_devices"
selected_devices=()
for index in "${logical_indexes[@]}"; do
    selected_devices+=("${available_devices[$index]}")
done
ASCEND_RT_VISIBLE_DEVICES=$(IFS=,; echo "${selected_devices[*]}")
export ASCEND_RT_VISIBLE_DEVICES

export HCCL_CONNECT_TIMEOUT=1200
export HCCL_BUFFSIZE=512
export OMP_PROC_BIND=false
export OMP_NUM_THREADS=1
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export TASK_QUEUE_ENABLE=1
export HCCL_OP_EXPANSION_MODE=AIV

engine_id=$((4 + dp_rank))
kv_transfer_config=$(cat <<EOF
{
  "kv_connector": "MooncakeConnectorV1",
  "kv_role": "kv_consumer",
  "kv_port": "30100",
  "engine_id": "$engine_id",
  "kv_connector_extra_config": {
    "prefill": {"dp_size": 4, "tp_size": 2},
    "decode": {"dp_size": 4, "tp_size": 2}
  }
}
EOF
)

exec vllm serve "$RECIPE_MODEL_PATH" \
    --host 0.0.0.0 \
    --port "$service_port" \
    --data-parallel-size "$dp_size" \
    --data-parallel-rank "$dp_rank" \
    --data-parallel-address "$dp_address" \
    --data-parallel-rpc-port "$dp_rpc_port" \
    --tensor-parallel-size "$tp_size" \
    --seed 1024 \
    --quantization ascend \
    --served-model-name "$RECIPE_SERVED_MODEL_NAME" \
    --max-num-seqs 32 \
    --max-model-len 133000 \
    --max-num-batched-tokens 256 \
    --trust-remote-code \
    --gpu-memory-utilization 0.90 \
    --no-enable-prefix-caching \
    --speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":3,"enforce_eager":true}' \
    --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
    --additional-config '{"enable_cpu_binding":true}' \
    --async-scheduling \
    --kv-transfer-config "$kv_transfer_config"
