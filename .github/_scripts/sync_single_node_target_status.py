#!/usr/bin/env python3
"""Publish exact single-node configuration results from recipe params snapshots.

Prefer explicit scenario outcomes, then each hardware run's legacy result.
Failures without scenario outcomes and selected hardware without scenarios are
unverified, while a legacy pass applies to its listed scenarios. Hardware skips
without explicit scenario outcomes leave earlier evidence alone.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
from typing import Any


SELECTOR_KEYS = ("npu", "precision", "deployment", "case")
RESULT_STATUSES = {"pass", "fail", "unknown"}
SCENARIO_STATUSES = RESULT_STATUSES | {"skip"}
# Hardware keys and NPU predicates used by verification_plan.single_node_scenarios.
HARDWARE_NPU_PATTERNS = {
    "atlas_800_a2": r"\bA2\b",
    "atlas_800_a3": r"\bA3\b",
    "ascend_950dt": r"\b950DT\b",
}


def selector_key(selector: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(selector.get("npu", "")),
        str(selector.get("precision", "")),
        str(selector.get("deployment", "")),
        str(selector.get("case", "")),
    )


def run_identity(run: dict[str, Any]) -> tuple[Any, ...]:
    if run.get("workflow_run_id") is not None:
        return (run.get("kind"), str(run["workflow_run_id"]))
    return (run.get("kind"), run.get("head_sha"), run.get("finished_at"))


def should_replace_latest(previous: Any, run: dict[str, Any]) -> bool:
    if not isinstance(previous, dict) or run_identity(previous) == run_identity(run):
        return True
    finished = str(run.get("finished_at") or "")
    previous_finished = str(previous.get("finished_at") or "")
    if finished and previous_finished and finished != previous_finished:
        return finished > previous_finished
    run_id = str(run.get("workflow_run_id", ""))
    previous_id = str(previous.get("workflow_run_id", ""))
    if run_id.isdigit() and previous_id.isdigit():
        return int(run_id) >= int(previous_id)
    return finished >= previous_finished


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
    if should_replace_latest(current.get(f"last_{trigger}_run"), run):
        current[f"last_{trigger}_run"] = copy.deepcopy(run)
    history = [
        entry for entry in current.get("history", [])
        if isinstance(entry, dict) and run_identity(entry) != run_identity(run)
    ]
    history.append(copy.deepcopy(run))
    current["history"] = sorted(history, key=lambda entry: str(entry.get("finished_at", "")), reverse=True)
    targets[target["id"]] = current
    return merged


def sync_target_status(status_dir: Path, runs_dir: Path, targets: list[dict[str, Any]]) -> list[str]:
    """Update whitelisted single-node target records from current model runs."""
    by_recipe_and_selector = {
        (target["recipe"], selector_key(target["selector"])): target
        for target in targets
        if target["mode"] == "single-node" and target.get("enabled", True)
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
            if not isinstance(run, dict) or run.get("status") not in SCENARIO_STATUSES:
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
            snapshot_run_id = params.get("workflow_run_id")
            if snapshot_run_id not in (None, "") and str(snapshot_run_id) != str(run.get("workflow_run_id")):
                continue  # A newer run can replace the archive at the same SHA.
            recipe = params.get("recipe_path")
            if not isinstance(recipe, str):
                continue
            hardware_runs = params.get("hardware_runs")
            sources = hardware_runs if isinstance(hardware_runs, list) else [
                {**params, "status": params.get("status", run["status"])}
            ]
            for source in sources:
                if not isinstance(source, dict):
                    continue
                scenarios = source.get("scenarios", [])
                if not isinstance(scenarios, list):
                    continue
                outcomes = []
                for scenario in scenarios:
                    if not isinstance(scenario, dict):
                        continue
                    target = by_recipe_and_selector.get((recipe, selector_key(scenario)))
                    if not target:
                        continue
                    outcome = scenario.get("verification_status")
                    if outcome not in SCENARIO_STATUSES:
                        if source.get("status") == "skip":
                            continue
                        outcome = "pass" if source.get("status") == "pass" else "unknown"
                    outcomes.append((target, outcome, scenario))
                if not scenarios and source.get("status") != "skip":
                    pattern = HARDWARE_NPU_PATTERNS.get(source.get("hw_key"))
                    if pattern:
                        outcomes.extend(
                            (target, "unknown", {})
                            for (target_recipe, selector), target in by_recipe_and_selector.items()
                            if target_recipe == recipe and re.search(pattern, selector[0], re.IGNORECASE)
                        )
                for target, outcome, scenario in outcomes:
                    exact_run = {**copy.deepcopy(run), "status": outcome}
                    # Model-wide audit notes may describe a different hardware.
                    # Only attach evidence recorded for this exact scenario.
                    exact_run.pop("verification_note", None)
                    exact_run.pop("verification_evidence_url", None)
                    if scenario.get("verification_reason"):
                        exact_run["verification_note"] = scenario["verification_reason"]
                    if scenario.get("verification_evidence_url"):
                        exact_run["verification_evidence_url"] = scenario["verification_evidence_url"]
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
