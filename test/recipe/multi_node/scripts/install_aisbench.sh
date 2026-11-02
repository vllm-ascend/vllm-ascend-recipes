#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
AIS_BENCH_PACKAGE_NAME=ais_bench_benchmark
AIS_BENCH_PACKAGE_VERSION=${AIS_BENCH_PACKAGE_VERSION:-3.1.20260630}
AIS_BENCH_CACHE_ROOT=${AIS_BENCH_CACHE_ROOT:-/root/.cache/multi-node/tools/aisbench}
AIS_BENCH_CACHE_SCHEMA=${AIS_BENCH_CACHE_SCHEMA:-v2}
AIS_BENCH_ENVIRONMENT_IDENTITY=${AIS_BENCH_ENVIRONMENT_IDENTITY:-}
AIS_BENCH_PYTHON=${AIS_BENCH_PYTHON:-python3}
AIS_BENCH_CONSTRAINTS=${AIS_BENCH_CONSTRAINTS:-$SCRIPT_DIR/aisbench-constraints.txt}
AIS_BENCH_PIP_RETRIES=${AIS_BENCH_PIP_RETRIES:-3}
env_file=""
staging_directory=""

# Print the intentionally small installer CLI.
usage() {
    echo "Usage: $0 [--env-file PATH]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            env_file=$2
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

[[ -n "$AIS_BENCH_ENVIRONMENT_IDENTITY" ]] || {
    echo "AIS_BENCH_ENVIRONMENT_IDENTITY is required." >&2
    exit 1
}
[[ -f "$AIS_BENCH_CONSTRAINTS" ]] || {
    echo "AISBench constraints not found: $AIS_BENCH_CONSTRAINTS" >&2
    exit 1
}
[[ "$AIS_BENCH_PACKAGE_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || {
    echo "AIS_BENCH_PACKAGE_VERSION contains unsupported characters." >&2
    exit 1
}
[[ "$AIS_BENCH_PIP_RETRIES" =~ ^(0|[1-9][0-9]*)$ ]] || {
    echo "AIS_BENCH_PIP_RETRIES must be a non-negative integer." >&2
    exit 1
}
python_version=$(
    "$AIS_BENCH_PYTHON" -c \
        'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
)
architecture=$(uname -m)
constraints_hash=$(
    "$AIS_BENCH_PYTHON" - "$AIS_BENCH_CONSTRAINTS" <<'PY'
import hashlib
import pathlib
import sys

print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()[:16])
PY
)
environment_hash=$(
    "$AIS_BENCH_PYTHON" - "$AIS_BENCH_ENVIRONMENT_IDENTITY" <<'PY'
import hashlib
import sys

print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])
PY
)
cache_key="${AIS_BENCH_CACHE_SCHEMA}-${AIS_BENCH_PACKAGE_NAME}-${AIS_BENCH_PACKAGE_VERSION}-env${environment_hash}-py${python_version}-${architecture}-${constraints_hash}"
cache_directory="${AIS_BENCH_CACHE_ROOT}/${cache_key}"
venv_directory="${cache_directory}/venv"
source_directory="${venv_directory}/lib/python${python_version}/site-packages"
command_path="${venv_directory}/bin/ais_bench"
ready_file="${cache_directory}/READY"

case "$cache_directory" in
    "$AIS_BENCH_CACHE_ROOT"/*) ;;
    *)
        echo "Unsafe AISBench cache directory: $cache_directory" >&2
        exit 1
        ;;
esac

# Verify every cache identity component before reuse.
cache_is_valid() {
    [[ -f "$ready_file" ]] || return 1
    [[ "$(<"$ready_file")" == "$cache_key" ]] || return 1
    [[ -d "$source_directory/ais_bench/benchmark/configs/datasets" ]] || return 1
    [[ -d "$source_directory/ais_bench/benchmark/configs/models/vllm_api" ]] || return 1
    [[ -x "$command_path" ]] || return 1
    "$command_path" -h >/dev/null 2>&1
}

# Publish shell assignments consumed by every node in the same run.
write_environment() {
    [[ -n "$env_file" ]] || return 0
    mkdir -p "$(dirname -- "$env_file")"
    {
        printf 'MULTI_NODE_AISBENCH_BIN=%s\n' "$command_path"
        printf 'MULTI_NODE_AISBENCH_CACHE_KEY=%s\n' "$cache_key"
        printf 'MULTI_NODE_AISBENCH_SOURCE=%s\n' "$source_directory"
    } > "$env_file"
}

if cache_is_valid; then
    echo "Reusing AISBench cache: $cache_directory"
    write_environment
    exit 0
fi

mkdir -p "$AIS_BENCH_CACHE_ROOT"
command -v flock >/dev/null || {
    echo "flock is required to prepare the shared AISBench cache." >&2
    exit 1
}
exec 9>"${AIS_BENCH_CACHE_ROOT}/.${cache_key}.lock"
flock 9

# A concurrent preparation may have populated the cache while this process
# waited for the per-key lock.
if cache_is_valid; then
    echo "Reusing AISBench cache: $cache_directory"
    write_environment
    exit 0
fi

if [[ -e "$cache_directory" ]]; then
    echo "Removing incomplete AISBench cache: $cache_directory"
    rm -rf -- "$cache_directory"
fi

staging_directory=$(mktemp -d "${AIS_BENCH_CACHE_ROOT}/.${cache_key}.tmp.XXXXXX")
# Remove only an unpublished per-key staging directory on interruption.
cleanup() {
    [[ -n "$staging_directory" && -d "$staging_directory" ]] || return 0
    rm -rf -- "$staging_directory"
}
trap cleanup EXIT

# Reuse the runtime image's framework dependencies while keeping AISBench in
# the shared cache. Kubernetes supplies the Huawei Cloud public Python package
# index through PIP_INDEX_URL; local runs use the caller's existing pip
# configuration.
"$AIS_BENCH_PYTHON" -m venv --system-site-packages "$staging_directory/venv"
echo "Installing ${AIS_BENCH_PACKAGE_NAME}==${AIS_BENCH_PACKAGE_VERSION}"
"$staging_directory/venv/bin/python" -m pip install \
    --retries "$AIS_BENCH_PIP_RETRIES" \
    --constraint "$AIS_BENCH_CONSTRAINTS" \
    "${AIS_BENCH_PACKAGE_NAME}==${AIS_BENCH_PACKAGE_VERSION}"

staging_source_directory="${staging_directory}/venv/lib/python${python_version}/site-packages"
[[ -d "$staging_source_directory/ais_bench/benchmark/configs/datasets" ]] || {
    echo "Installed AISBench package does not contain dataset templates." >&2
    exit 1
}
[[ -d "$staging_source_directory/ais_bench/benchmark/configs/models/vllm_api" ]] || {
    echo "Installed AISBench package does not contain vLLM API templates." >&2
    exit 1
}

# Console-script shebangs contain the temporary venv path. Keep the generated
# Python entry point and invoke it through the adjacent, relocatable interpreter.
# The renamed file must not be ais_bench.py: placing that name in venv/bin would
# shadow the installed ais_bench package when Python initializes sys.path.
mv "$staging_directory/venv/bin/ais_bench" \
    "$staging_directory/venv/bin/_ais_bench_entrypoint.py"
cat > "$staging_directory/venv/bin/ais_bench" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
bin_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$bin_directory/python" "$bin_directory/_ais_bench_entrypoint.py" "$@"
SH
chmod +x "$staging_directory/venv/bin/ais_bench"
"$staging_directory/venv/bin/ais_bench" -h >/dev/null

printf '%s\n' "$cache_key" > "$staging_directory/READY"
mv "$staging_directory" "$cache_directory"
staging_directory=""
write_environment

echo "AISBench prepared: $cache_directory"
