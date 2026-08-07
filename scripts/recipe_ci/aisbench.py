#!/usr/bin/env python3
"""Preflight AISBench and translate its artifacts to the step-result contract."""

from __future__ import annotations

import argparse
import csv
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.recipe_ci.result import write_json_atomic  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument(
        "--command",
        default=os.environ.get("RECIPE_AISBENCH_BIN", "ais_bench"),
    )
    preflight.add_argument("--model-config", type=Path, required=True)
    preflight.add_argument("--dataset-directory", type=Path, required=True)
    preflight.add_argument("--artifact-directory", type=Path, required=True)

    accuracy = subparsers.add_parser("accuracy")
    accuracy.add_argument("--artifact-directory", type=Path, required=True)
    accuracy.add_argument("--result-file", type=Path, required=True)
    accuracy.add_argument("--baseline", type=float)
    accuracy.add_argument("--allowed-drop", type=float, default=0.0)

    performance = subparsers.add_parser("performance")
    performance.add_argument("--artifact-directory", type=Path, required=True)
    performance.add_argument("--result-file", type=Path, required=True)

    render = subparsers.add_parser("render-model-config")
    render.add_argument("--template", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def newest_files(directory: Path, suffix: str) -> list[Path]:
    return sorted(
        directory.rglob(f"*{suffix}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def render_model_config(template: Path, output: Path) -> None:
    substitutions = {
        "__RECIPE_MODEL_PATH__": ("RECIPE_MODEL_PATH", None, False),
        "__RECIPE_SERVED_MODEL_NAME__": (
            "RECIPE_SERVED_MODEL_NAME",
            None,
            False,
        ),
        "__RECIPE_ENDPOINT_HOST__": ("RECIPE_ENDPOINT_HOST", None, False),
        "__RECIPE_ENDPOINT_PORT__": ("RECIPE_ENDPOINT_PORT", None, True),
        "__RECIPE_AISBENCH_MAX_OUT_LEN__": (
            "RECIPE_AISBENCH_MAX_OUT_LEN",
            "512",
            True,
        ),
    }
    content = template.read_text(encoding="utf-8")
    for placeholder, (name, default, numeric) in substitutions.items():
        value = os.environ.get(name, default)
        if not value:
            raise RuntimeError(f"required environment variable is missing: {name}")
        replacement = str(int(value)) if numeric else repr(value)
        content = content.replace(placeholder, replacement)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")


def preflight(args: argparse.Namespace) -> None:
    command = shutil.which(args.command) if "/" not in args.command else args.command
    if not command or not Path(command).is_file():
        raise RuntimeError(f"AISBench command not found: {args.command}")
    subprocess.run(
        [str(command), "-h"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    if not args.model_config.is_file():
        raise RuntimeError(f"AISBench model config not found: {args.model_config}")
    for name in (
        "RECIPE_ENDPOINT_HOST",
        "RECIPE_ENDPOINT_PORT",
        "RECIPE_MODEL_PATH",
        "RECIPE_SERVED_MODEL_NAME",
    ):
        if not os.environ.get(name):
            raise RuntimeError(f"required environment variable is missing: {name}")
    runpy.run_path(str(args.model_config))
    if not args.dataset_directory.is_dir():
        raise RuntimeError(
            f"AISBench dataset directory not found: {args.dataset_directory}"
        )
    args.artifact_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.artifact_directory):
        pass


def accuracy_score(directory: Path) -> tuple[float, Path]:
    standard_columns = {"dataset", "version", "metric", "mode", "total_count"}
    for path in newest_files(directory, ".csv"):
        with path.open(newline="", encoding="utf-8-sig") as input_file:
            for row in csv.DictReader(input_file):
                if str(row.get("metric", "")).lower() != "accuracy":
                    continue
                for column, value in row.items():
                    if column not in standard_columns and value not in (None, ""):
                        try:
                            return float(value), path
                        except ValueError:
                            continue
    raise RuntimeError(f"no accuracy metric found under {directory}")


def flatten_json(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_json(item, name)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from flatten_json(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        token = value.strip().split()[0].replace(",", "") if value.strip() else ""
        try:
            return float(token)
        except ValueError:
            return None
    return None


def performance_metrics(directory: Path) -> tuple[dict[str, float], list[Path]]:
    metrics: dict[str, float] = {}
    sources: list[Path] = []
    aliases = {
        "request throughput": "request_per_second",
        "output token throughput": "output_token_per_second",
        "e2e latency": "e2e_latency_ms",
        "e2el": "e2e_latency_ms",
        "ttft": "ttft_ms",
        "tpot": "tpot_ms",
    }

    for path in newest_files(directory, ".json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        found = False
        for key, raw_value in flatten_json(value):
            lowered = key.lower()
            for phrase, metric_name in aliases.items():
                if phrase in lowered and (parsed := number(raw_value)) is not None:
                    metrics.setdefault(metric_name, parsed)
                    found = True
        if found:
            sources.append(path)

    for path in newest_files(directory, ".csv"):
        try:
            with path.open(newline="", encoding="utf-8-sig") as input_file:
                rows = list(csv.DictReader(input_file))
        except (OSError, csv.Error):
            continue
        found = False
        for row in rows:
            parameter = str(row.get("Performance Parameters", "")).lower()
            average = number(row.get("Average"))
            if average is None:
                continue
            for phrase, metric_name in aliases.items():
                if phrase in parameter:
                    metrics.setdefault(metric_name, average)
                    found = True
        if found:
            sources.append(path)

    required = {
        "request_per_second",
        "output_token_per_second",
        "e2e_latency_ms",
        "ttft_ms",
        "tpot_ms",
    }
    missing = sorted(required - set(metrics))
    if missing:
        raise RuntimeError(
            f"performance metrics missing under {directory}: {', '.join(missing)}"
        )
    return metrics, list(dict.fromkeys(sources))


def relative_artifacts(paths: Iterable[Path], root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in paths]


def main() -> int:
    args = parse_args()
    try:
        if args.action == "render-model-config":
            render_model_config(args.template, args.output)
            return 0
        if args.action == "preflight":
            preflight(args)
            return 0
        if args.action == "accuracy":
            score, source = accuracy_score(args.artifact_directory)
            metrics: dict[str, float] = {"accuracy": score}
            status = "passed"
            if args.baseline is not None:
                metrics.update(
                    baseline=args.baseline,
                    allowed_drop=args.allowed_drop,
                )
                if score < args.baseline - args.allowed_drop:
                    status = "failed"
            write_json_atomic(
                args.result_file,
                {
                    "status": status,
                    "type": "accuracy",
                    "mode": "gate" if args.baseline is not None else "smoke",
                    "metrics": metrics,
                    "artifacts": relative_artifacts(
                        [source], args.artifact_directory
                    ),
                },
            )
            if status == "failed":
                raise RuntimeError(
                    f"accuracy {score} is below {args.baseline - args.allowed_drop}"
                )
            return 0

        metrics, sources = performance_metrics(args.artifact_directory)
        write_json_atomic(
            args.result_file,
            {
                "status": "passed",
                "type": "performance",
                "metrics": metrics,
                "artifacts": relative_artifacts(sources, args.artifact_directory),
            },
        )
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ImportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
