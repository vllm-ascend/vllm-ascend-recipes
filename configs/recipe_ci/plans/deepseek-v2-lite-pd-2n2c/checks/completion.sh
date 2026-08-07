#!/usr/bin/env bash
set -euo pipefail

response=$(curl --fail --silent --show-error \
    "$RECIPE_ENDPOINT/v1/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$RECIPE_SERVED_MODEL_NAME\",\"prompt\":\"The future of AI is\",\"max_tokens\":50,\"temperature\":0}")

python3 -c 'import json, sys; assert json.load(sys.stdin)["choices"]' <<<"$response"
