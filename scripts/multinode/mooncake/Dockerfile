# Builds a Mooncake-enabled vllm-ascend image for the multi-node PD pipeline.
#
# The recipe's `--kv-transfer-config '{"kv_connector": "MooncakeHybridConnector", ...}'`
# needs the Mooncake C++ libs plus the `mooncake` python package (`from
# mooncake.engine import TransferEngine`) inside the runtime image. Building from
# source is heavy (system deps + yalantinglibs + Go + cmake/make with
# USE_ASCEND_DIRECT=ON), so we do it ONCE here and tag the result
# `vllm-ascend:<ver>-mooncake`. The pipeline takes `--image`; point it at this
# image for PD scenarios (default docker image in the recipe is also Mooncake
# based for the A3 variant).
#
# Build once per base version (on a node that can pull the base image and
# push to your registry):
#   scripts/multinode/mooncake/build.sh
#
# Dependencies installer: upstream vllm-ascend tools/mooncake_installer.sh
# (installs apt packages, yalantinglibs, Go, git submodules — does NOT build
# Mooncake itself, so we add the cmake/make steps below).
ARG BASE_IMAGE=swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:v0.23.0rc1
FROM ${BASE_IMAGE}

ARG VLLM_ASCEND_TAG=v0.23.0rc1
ARG MOONCAKE_VERSION=v0.3.9
ARG GITHUB_PROXY=https://github.com

WORKDIR /opt

# git is needed to clone below; the installer's apt step brings the rest of the
# build toolchain (cmake/gcc/mpich/...).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 1. vllm-ascend source: its installer pulls all Mooncake build dependencies.
#    We keep the checkout so the P-D proxy examples are available for
#    `proxy_command` (examples/disaggregated_prefill_v1/).
RUN git clone --depth 1 --branch ${VLLM_ASCEND_TAG} ${GITHUB_PROXY}/vllm-project/vllm-ascend.git /opt/vllm-ascend \
 && cd /opt/vllm-ascend \
 && bash tools/mooncake_installer.sh -y

# 2. Build + install Mooncake itself (AscendDirectTransport for RDMA on NPU).
#    `make install` puts the python package under site-packages/mooncake.
RUN git clone --depth 1 --branch ${MOONCAKE_VERSION} ${GITHUB_PROXY}/kvcache-ai/Mooncake.git /opt/Mooncake \
 && cd /opt/Mooncake \
 && bash dependencies.sh -y \
 && mkdir -p build && cd build \
 && cmake .. -DUSE_ASCEND_DIRECT=ON \
 && make -j$(nproc) \
 && make install

ENV PATH=/usr/local/go/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/lib64/python3.12/site-packages/mooncake:${LD_LIBRARY_PATH}
