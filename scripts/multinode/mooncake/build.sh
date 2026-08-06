#!/usr/bin/env bash
# Build the Mooncake-enabled vllm-ascend image once per base version.
#
# Usage:
#   scripts/multinode/mooncake/build.sh [BASE_IMAGE] [OUT_TAG]
#
# Defaults:
#   BASE_IMAGE = the base vllm-ascend image (must already exist / be pullable)
#   OUT_TAG    = BASE_IMAGE with `-mooncake` appended to the tag
#
# After building, push OUT_TAG to the registry the cluster pulls from, then pass
# it as the `image` input of the `Multi-node Recipe Verify` workflow.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

BASE_IMAGE="${1:-swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:v0.23.0rc1}"
# append `-mooncake` to the last `:`-delimited tag segment
OUT_TAG="${2:-${BASE_IMAGE%:*}-mooncake}"

echo "==> Building $OUT_TAG from base $BASE_IMAGE"
docker build \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -t "$OUT_TAG" \
  -f "$HERE/Dockerfile" \
  "$HERE"

echo "==> Done: $OUT_TAG"
echo "    Push it to the registry the a2b4 cluster pulls from, then run the"
echo "    workflow with image = $OUT_TAG"
