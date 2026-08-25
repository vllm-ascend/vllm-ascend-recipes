#!/usr/bin/env python3
"""Materialize failed nightly results for verification targets without an artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def fill_missing_results(
    targets: list[dict[str, Any]], output_dir: Path, target_ids: set[str] | None = None
) -> None:
    """Write a failed result only when the target has no uploaded result file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for target in targets:
        if target["mode"] != "multi-node":
            continue
        if target_ids is not None and target["id"] not in target_ids:
            continue
        output_file = output_dir / f'{target["id"]}.json'
        if output_file.exists():
            continue
        output_file.write_text(
            json.dumps(
                {
                    "target_id": target["id"],
                    "recipe": target["recipe"],
                    "test_id": target["test_id"],
                    "status": "fail",
                }
            ),
            encoding="utf-8",
        )


def load_targets(path: Path) -> list[dict[str, Any]]:
    script = path.parent / "_scripts" / "verification_targets.py"
    spec = importlib.util.spec_from_file_location("verification_targets", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_targets(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-id", action="append", default=[])
    args = parser.parse_args()
    fill_missing_results(
        load_targets(args.targets),
        args.output_dir,
        set(args.target_id) if args.target_id else None,
    )


if __name__ == "__main__":
    main()
