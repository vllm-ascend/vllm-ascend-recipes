#!/usr/bin/env python3
"""Reconstruct exact configuration history from committed status snapshots.

Status files retain the latest PR/nightly pointer at every status-publisher
commit.  This script walks those snapshots, pairs each pointer with the params
file committed alongside it, and records only scenarios matching the explicit
target registry.  Raw params without a committed outcome are intentionally
ignored.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any


SELECTOR_KEYS = ("npu", "precision", "deployment", "case")


def git_show(repo: Path, revision: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{revision}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def selector_key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(value.get(key, "")) for key in SELECTOR_KEYS)  # type: ignore[return-value]


def run_identity(run: dict[str, Any]) -> tuple[Any, ...]:
    return (run.get("kind"), run.get("workflow_run_id") or run.get("head_sha"), run.get("finished_at"))


def add_history(target_status: dict[str, Any], run: dict[str, Any]) -> None:
    history = [entry for entry in target_status.get("history", []) if isinstance(entry, dict)]
    if run_identity(run) not in {run_identity(entry) for entry in history}:
        history.append(copy.deepcopy(run))
    target_status["history"] = sorted(history, key=lambda entry: str(entry.get("finished_at", "")), reverse=True)


def history_commits(repo: Path, status_path: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "--", status_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return [commit for commit in result.stdout.splitlines() if commit]


def backfill_target_history(repo: Path, status_dir: Path, targets: list[dict[str, Any]]) -> list[str]:
    by_recipe_selector = {
        (target["recipe"], selector_key(target["selector"])): target for target in targets
    }
    target_slugs = {Path(target["recipe"]).stem for target in targets}
    changed: list[str] = []
    for status_file in sorted(status_dir.glob("*.json")):
        if status_file.name == "index.json":
            continue
        current = json.loads(status_file.read_text(encoding="utf-8"))
        slug = status_file.stem
        if slug not in target_slugs:
            continue
        updated = False
        for commit in history_commits(repo, f"public/status/{slug}.json"):
            raw_status = git_show(repo, commit, f"public/status/{slug}.json")
            if not raw_status:
                continue
            snapshot = json.loads(raw_status)
            for kind in ("pr", "nightly"):
                run = snapshot.get(f"last_{kind}_run")
                if not isinstance(run, dict) or run.get("status") not in {"pass", "fail"}:
                    continue
                head_sha = run.get("head_sha")
                if not isinstance(head_sha, str) or not head_sha:
                    continue
                raw_params = git_show(repo, commit, f"public/runs/{head_sha}/{slug}.params.json")
                if not raw_params:
                    continue
                params = json.loads(raw_params)
                recipe = params.get("recipe_path")
                if not isinstance(recipe, str):
                    continue
                for scenario in params.get("scenarios", []):
                    if not isinstance(scenario, dict):
                        continue
                    target = by_recipe_selector.get((recipe, selector_key(scenario)))
                    if not target:
                        continue
                    target_status = current.setdefault("targets", {}).setdefault(target["id"], {})
                    target_status.update(
                        {
                            "test_id": target.get("test_id"),
                            "selector": target["selector"],
                            "runner": target["runner"],
                            "mode": target["mode"],
                        }
                    )
                    exact_run = copy.deepcopy(run)
                    if exact_run["status"] == "fail":
                        exact_run["status"] = "unknown"
                    before = target_status.get("history", [])
                    add_history(target_status, exact_run)
                    updated = updated or target_status.get("history", []) != before
        if updated:
            status_file.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            changed.append(slug)
    return changed


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
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--status-dir", type=Path, default=Path("public/status"))
    parser.add_argument("--targets", type=Path, default=Path(".github/verification-targets.yaml"))
    args = parser.parse_args()
    updated = backfill_target_history(args.repo, args.status_dir, load_targets(args.targets))
    print(f"Backfilled exact history for {len(updated)} model(s).")


if __name__ == "__main__":
    main()
