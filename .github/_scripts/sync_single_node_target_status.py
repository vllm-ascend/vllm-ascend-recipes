#!/usr/bin/env python3
"""Publish exact single-node configuration results from recipe params snapshots.

Recipe Deployment Verification produces one recipe-level result.  Its params
snapshot enumerates every compatible scenario that was run, so a recipe-level
pass applies to each listed scenario.  A recipe-level failure does not identify
the failed scenario, so it clears exact verification evidence as ``unknown``
rather than falsely publishing every listed scenario as failed.  A skip has no
per-scenario result and must not replace earlier evidence.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any


SELECTOR_KEYS = ("npu", "precision", "deployment", "case")
RESULT_STATUSES = {"pass", "fail"}


def selector_key(selector: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(selector.get("npu", "")),
        str(selector.get("precision", "")),
        str(selector.get("deployment", "")),
        str(selector.get("case", "")),
    )


def merge_target(existing: dict[str, Any], target: dict[str, Any], run: dict[str, Any], trigger: str) -> dict[str, Any]:
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
    targets[target["id"]] = current
    return merged


def sync_target_status(status_dir: Path, runs_dir: Path, targets: list[dict[str, Any]]) -> list[str]:
    """Update whitelisted single-node target records from current model runs."""
    by_recipe_and_selector = {
        (target["recipe"], selector_key(target["selector"])): target
        for target in targets
        if target["mode"] == "single-node"
    }
    updated: list[str] = []

    for status_file in sorted(status_dir.glob("*.json")):
        if status_file.name == "index.json":
            continue
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        changed = False
        for trigger in ("pr", "nightly"):
            run = status.get(f"last_{trigger}_run")
            if not isinstance(run, dict) or run.get("status") not in RESULT_STATUSES:
                continue
            head_sha = run.get("head_sha")
            if not isinstance(head_sha, str) or not head_sha:
                continue
            params_file = runs_dir / head_sha / f"{status_file.stem}.params.json"
            if not params_file.is_file():
                continue
            try:
                params = json.loads(params_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            recipe = params.get("recipe_path")
            scenarios = params.get("scenarios")
            if not isinstance(recipe, str) or not isinstance(scenarios, list):
                continue
            for scenario in scenarios:
                if not isinstance(scenario, dict):
                    continue
                target = by_recipe_and_selector.get((recipe, selector_key(scenario)))
                if not target:
                    continue
                exact_run = copy.deepcopy(run)
                if exact_run["status"] == "fail":
                    exact_run["status"] = "unknown"
                merged = merge_target(status, target, exact_run, trigger)
                if merged != status:
                    status = merged
                    changed = True
        if changed:
            status_file.write_text(
                json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            updated.append(status_file.stem)
    return updated


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
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    args = parser.parse_args()

    updated = sync_target_status(args.status_dir, args.runs_dir, load_targets(args.targets))
    print(f"Synced single-node configuration status for {len(updated)} model(s).")


if __name__ == "__main__":
    main()
