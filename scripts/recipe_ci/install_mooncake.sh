#!/usr/bin/env bash
set -euo pipefail

# Install the Ascend NPU Mooncake transfer engine via pip and print the
# directory containing its shared libraries to stdout. All other output is
# sent to stderr so the caller can capture the library path directly:
#
#     lib_dir=$("$SCRIPT_DIR/install_mooncake.sh")
#     export LD_LIBRARY_PATH="${lib_dir}:${LD_LIBRARY_PATH:-}"
#
# Mooncake is required by the MooncakeConnector / MooncakeHybridConnector KV
# transfer backends used in PD disaggregation. Reference:
# https://github.com/kvcache-ai/Mooncake  (PyPI: mooncake-transfer-engine-npu)

MOONCAKE_PACKAGE=${MOONCAKE_PACKAGE:-mooncake-transfer-engine-npu}

if python3 -c "import mooncake" 2>/dev/null; then
    echo "Mooncake is already installed." >&2
else
    echo "Installing Mooncake ($MOONCAKE_PACKAGE)..." >&2
    pip install "$MOONCAKE_PACKAGE" >&2
fi

python3 -c "import mooncake, os; print(os.path.dirname(mooncake.__file__))"
