#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
AIS_BENCH_TAG=${AIS_BENCH_TAG:-v3.1-20260609-master}
AIS_BENCH_EXPECTED_COMMIT=${AIS_BENCH_EXPECTED_COMMIT:-0da56eadb2ac85c31c2540f4f5b69af3ec5717a5}
AIS_BENCH_URL=${AIS_BENCH_URL:-https://github.com/AISBench/benchmark.git}
VLLM_ASCEND_ROOT=${VLLM_ASCEND_ROOT:-/vllm-workspace/vllm-ascend}
AIS_BENCH_ROOT=${AIS_BENCH_ROOT:-$VLLM_ASCEND_ROOT/benchmark}
AIS_BENCH_PYTHON=python3
AIS_BENCH_COMMAND=ais_bench
force_reinstall=false
source_ready=false
actual_commit=""

usage() {
    echo "Usage: $0 [--force-reinstall]"
}

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi
if [[ $# -eq 1 ]]; then
    case "$1" in
        --force-reinstall) force_reinstall=true ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
fi

if [[ -n "${AIS_BENCH_VENV:-}" ]]; then
    if [[ ! -x "$AIS_BENCH_VENV/bin/python" ]]; then
        python3 -m venv "$AIS_BENCH_VENV"
    fi
    AIS_BENCH_PYTHON="$AIS_BENCH_VENV/bin/python"
    AIS_BENCH_COMMAND="$AIS_BENCH_VENV/bin/ais_bench"
fi

if [[ -e "$AIS_BENCH_ROOT" ]]; then
    current_commit=""
    current_url=""
    if [[ -d "$AIS_BENCH_ROOT/.git" ]]; then
        current_commit=$(git -C "$AIS_BENCH_ROOT" rev-parse HEAD)
        current_url=$(git -C "$AIS_BENCH_ROOT" remote get-url origin)
    fi

    if [[ "$current_commit" == "$AIS_BENCH_EXPECTED_COMMIT" && "$force_reinstall" == false ]]; then
        if [[ "$current_url" != "$AIS_BENCH_URL" ]]; then
            echo "AISBench origin does not match AIS_BENCH_URL." >&2
            echo "  expected: $AIS_BENCH_URL" >&2
            echo "  actual:   $current_url" >&2
            exit 1
        fi
        if ! git -C "$AIS_BENCH_ROOT" diff --quiet || \
            ! git -C "$AIS_BENCH_ROOT" diff --cached --quiet; then
            echo "AISBench tracked files are modified: $AIS_BENCH_ROOT" >&2
            exit 1
        fi
        if resolved_command=$(command -v "$AIS_BENCH_COMMAND") && \
            "$resolved_command" -h >/dev/null; then
            echo "AISBench is already installed at the expected commit."
            echo "  tag:        $AIS_BENCH_TAG"
            echo "  commit:     $current_commit"
            echo "  repository: $current_url"
            echo "  source:     $AIS_BENCH_ROOT"
            echo "  command:    $resolved_command"
            exit 0
        fi
        echo "Reusing cached AISBench source; installing the command into this image."
        source_ready=true
        actual_commit=$current_commit
    fi

    if [[ "$source_ready" == false && "$force_reinstall" == false ]]; then
        echo "AISBench source exists at an unexpected version: $AIS_BENCH_ROOT" >&2
        echo "  expected: $AIS_BENCH_EXPECTED_COMMIT" >&2
        echo "  actual:   ${current_commit:-not a git checkout}" >&2
        echo "Run again with --force-reinstall to replace it." >&2
        exit 1
    fi

    if [[ "$source_ready" == false ]]; then
        case "$AIS_BENCH_ROOT" in
            "" | / | /vllm-workspace | "$VLLM_ASCEND_ROOT")
                echo "Refusing to remove unsafe AIS_BENCH_ROOT: $AIS_BENCH_ROOT" >&2
                exit 1
                ;;
        esac
        rm -rf -- "$AIS_BENCH_ROOT"
    fi
fi

if [[ "$source_ready" == false ]]; then
    mkdir -p "$(dirname "$AIS_BENCH_ROOT")"
    clone_root="${AIS_BENCH_ROOT}.clone.$$"
    cloned=false
    for attempt in 1 2 3; do
        echo "Cloning AISBench (attempt $attempt/3)..."
        if git -c http.version=HTTP/1.1 clone \
            --branch "$AIS_BENCH_TAG" --depth 1 \
            "$AIS_BENCH_URL" "$clone_root"; then
            cloned=true
            break
        fi
        rm -rf -- "$clone_root"
        sleep $((attempt * 2))
    done
    if [[ "$cloned" == false ]]; then
        echo "Unable to clone AISBench after 3 attempts." >&2
        exit 1
    fi

    actual_commit=$(git -C "$clone_root" rev-parse HEAD)
    if [[ "$actual_commit" != "$AIS_BENCH_EXPECTED_COMMIT" ]]; then
        rm -rf -- "$clone_root"
        echo "AISBench tag resolved to an unexpected commit." >&2
        echo "  expected: $AIS_BENCH_EXPECTED_COMMIT" >&2
        echo "  actual:   $actual_commit" >&2
        exit 1
    fi
    mv "$clone_root" "$AIS_BENCH_ROOT"
fi

"$AIS_BENCH_PYTHON" -m pip install \
    --editable "$AIS_BENCH_ROOT" \
    --constraint "$SCRIPT_DIR/aisbench-constraints.txt" \
    --requirement "$AIS_BENCH_ROOT/requirements/api.txt" \
    --requirement "$AIS_BENCH_ROOT/requirements/extra.txt"

resolved_command=$(command -v "$AIS_BENCH_COMMAND")
"$resolved_command" -h >/dev/null

echo "AISBench installed successfully."
echo "  tag:        $AIS_BENCH_TAG"
echo "  commit:     $actual_commit"
echo "  repository: $AIS_BENCH_URL"
echo "  source:     $AIS_BENCH_ROOT"
echo "  python:     $AIS_BENCH_PYTHON ($("$AIS_BENCH_PYTHON" --version 2>&1))"
echo "  pip:        $("$AIS_BENCH_PYTHON" -m pip --version)"
echo "  command:    $resolved_command"
if [[ -n "${AIS_BENCH_VENV:-}" ]]; then
    echo "Set RECIPE_AISBENCH_BIN=$AIS_BENCH_COMMAND before running evaluations."
fi
