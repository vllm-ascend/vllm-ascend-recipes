#!/usr/bin/env python3
"""Ensure every allowlisted configuration exists in its model status file."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def seed_target_statuses(status_dir: Path, targets: list[dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for target in targets:
        slug = Path(target["recipe"]).stem
        status_file = status_dir / f"{slug}.json"
        if not status_file.is_file():
            continue
        status = json.loads(status_file.read_text(encoding="utf-8"))
        entries = status.setdefault("targets", {})
        current = dict(entries.get(target["id"], {}))
        seeded = {
            **current,
            "test_id": target.get("test_id"),
            "selector": target["selector"],
            "runner": target["runner"],
            "mode": target["mode"],
            "last_pr_run": current.get("last_pr_run"),
            "last_nightly_run": current.get("last_nightly_run"),
            "last_manual_run": current.get("last_manual_run"),
            "history": current.get("history", []),
        }
        if seeded == current:
            continue
        entries[target["id"]] = seeded
        status_file.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    args = parser.parse_args()
    print(f"Seeded {len(seed_target_statuses(args.status_dir, load_targets(args.targets)))} model status file(s).")


if __name__ == "__main__":
    main()
