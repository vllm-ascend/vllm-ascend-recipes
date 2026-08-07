#!/usr/bin/env bash
set -euo pipefail

response=$(curl --fail --silent --show-error \
    "$RECIPE_ENDPOINT/v1/completions" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$RECIPE_SERVED_MODEL_NAME\",
        \"prompt\": \"The future of AI is\",
        \"max_tokens\": 50,
        \"temperature\": 0
    }")

RESPONSE=$response python3 -c '
import json
import os

payload = json.loads(os.environ["RESPONSE"])
if not payload.get("choices"):
    raise SystemExit("response does not contain choices")
'
