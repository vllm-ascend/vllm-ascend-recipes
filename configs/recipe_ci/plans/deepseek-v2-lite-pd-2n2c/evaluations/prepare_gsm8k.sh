#!/usr/bin/env bash
set -euo pipefail

aisbench_root=${RECIPE_AISBENCH_ROOT:-$RECIPE_VLLM_ASCEND_ROOT/benchmark}
source_directory=$RECIPE_PLAN_DIR/aisbench/datasets/gsm8k
dataset_directory=$aisbench_root/ais_bench/datasets/gsm8k

# AISBench's built-in GSM8K configs use this source-relative location. Link the
# small plan fixture there without downloading or copying a full dataset.
if [[ ! -e "$dataset_directory" && ! -L "$dataset_directory" ]]; then
    mkdir -p "$(dirname "$dataset_directory")"
    ln -s "$source_directory" "$dataset_directory"
fi
