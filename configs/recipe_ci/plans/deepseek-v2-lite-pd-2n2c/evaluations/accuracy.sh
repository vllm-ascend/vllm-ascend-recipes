#!/usr/bin/env bash
set -euo pipefail

aisbench_root=${RECIPE_AISBENCH_ROOT:-$RECIPE_VLLM_ASCEND_ROOT/benchmark}
template_config_dir=${RECIPE_AISBENCH_CONFIG_DIR:-$RECIPE_PLAN_DIR/aisbench}
runtime_config_dir=$RECIPE_STEP_ARTIFACT_DIR/aisbench-config
model_config=${RECIPE_AISBENCH_ACCURACY_MODEL_CONFIG:-vllm_api_general_chat}
dataset_directory=$aisbench_root/ais_bench/datasets/gsm8k

"$RECIPE_PLAN_DIR/evaluations/prepare_gsm8k.sh"
python3 "$RECIPE_REPOSITORY_ROOT/scripts/recipe_ci/aisbench.py" render-model-config \
    --template "$template_config_dir/models/$model_config.py" \
    --output "$runtime_config_dir/models/$model_config.py"

python3 "$RECIPE_REPOSITORY_ROOT/scripts/recipe_ci/aisbench.py" preflight \
    --command "${RECIPE_AISBENCH_BIN:-ais_bench}" \
    --model-config "$runtime_config_dir/models/$model_config.py" \
    --dataset-directory "$dataset_directory" \
    --artifact-directory "$RECIPE_STEP_ARTIFACT_DIR"

cd "$RECIPE_STEP_ARTIFACT_DIR"

"${RECIPE_AISBENCH_BIN:-ais_bench}" \
    --config-dir "$runtime_config_dir" \
    --models "$model_config" \
    --datasets gsm8k_gen_0_shot_cot_chat_prompt \
    --mode all \
    --num-prompts "${RECIPE_AISBENCH_ACCURACY_NUM_PROMPTS:-8}" \
    --dump-eval-details \
    --debug

collector=(
    python3 "$RECIPE_REPOSITORY_ROOT/scripts/recipe_ci/aisbench.py" accuracy
    --artifact-directory "$RECIPE_STEP_ARTIFACT_DIR"
    --result-file "$RECIPE_STEP_RESULT_FILE"
)
if [[ -n "${RECIPE_AISBENCH_ACCURACY_BASELINE:-}" ]]; then
    collector+=(
        --baseline "$RECIPE_AISBENCH_ACCURACY_BASELINE"
        --allowed-drop "${RECIPE_AISBENCH_ACCURACY_ALLOWED_DROP:-0}"
    )
fi
"${collector[@]}"
