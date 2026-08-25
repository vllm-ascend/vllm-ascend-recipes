#!/usr/bin/env python3
"""Merge one configuration-level verification result into a model status JSON."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


def run_identity(run: dict[str, Any]) -> tuple[Any, ...]:
    """Identify one workflow result while tolerating legacy incomplete records."""
    workflow_id = run.get("workflow_run_id")
    if workflow_id is not None:
        return (run.get("kind"), workflow_id)
    return (run.get("kind"), run.get("head_sha"), run.get("finished_at"), run.get("status"))


def merge_history(current: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
    history = [entry for entry in current.get("history", []) if isinstance(entry, dict)]
    if run_identity(run) not in {run_identity(entry) for entry in history}:
        history.append(copy.deepcopy(run))
    return sorted(history, key=lambda entry: str(entry.get("finished_at", "")), reverse=True)


def merge_target_status(
    existing: dict[str, Any], target: dict[str, Any], run: dict[str, Any], trigger: str
) -> dict[str, Any]:
    """Preserve model and sibling-target history while replacing one source record."""
    merged = copy.deepcopy(existing)
    targets = merged.setdefault("targets", {})
    current = dict(targets.get(target["id"], {}))
    current.update(
        {
            "test_id": target.get("test_id"),
            "selector": target["selector"],
            "runner": target["runner"],
            "mode": target["mode"],
        }
    )
    current[f"last_{trigger}_run"] = run
    current["history"] = merge_history(current, run)
    targets[target["id"]] = current
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--target-file", type=Path, required=True)
    parser.add_argument("--run-file", type=Path, required=True)
    parser.add_argument("--trigger", choices=("pr", "nightly", "manual"), required=True)
    args = parser.parse_args()

    existing = json.loads(args.status_file.read_text(encoding="utf-8"))
    target = json.loads(args.target_file.read_text(encoding="utf-8"))
    run = json.loads(args.run_file.read_text(encoding="utf-8"))
    merged = merge_target_status(existing, target, run, args.trigger)
    args.status_file.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
