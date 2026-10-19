#!/usr/bin/env python3
"""Resolve explicitly selected multi-node targets before allocating runners."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re

import yaml


def build_plan(root: Path, trigger: str, target_ids: str = "") -> dict:
    """Return one reusable-workflow matrix entry per validated target."""
    root = root.resolve()
    if trigger not in {"nightly", "manual"}:
        raise ValueError(f"unsupported trigger: {trigger}")
    if target_ids.strip():
        if trigger != "manual":
            raise ValueError("target ID overrides require a manual run")
        selected = [item.strip() for item in target_ids.split(",")]
        if any(not item for item in selected):
            raise ValueError("manual selection contains an empty target ID")
    else:
        config = yaml.safe_load((root / ".github/nightly-recipes.yaml").read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("multi_node"), list):
            raise ValueError("nightly config must contain a multi_node target ID list")
        selected = config["multi_node"]
        if any(not isinstance(item, str) or not item.strip() for item in selected):
            raise ValueError("multi_node must contain non-empty target ID strings")
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate selected target ID")

    registry = yaml.safe_load((root / ".github/verification-targets.yaml").read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or not isinstance(registry.get("targets"), list):
        raise ValueError("verification registry must contain a targets list")
    targets = {}
    for target in registry["targets"]:
        if not isinstance(target, dict) or not isinstance(target.get("id"), str) or not target["id"]:
            raise ValueError("registry targets must have a non-empty string id")
        if target["id"] in targets:
            raise ValueError(f"duplicate registry target ID: {target['id']}")
        targets[target["id"]] = target

    include = []
    for target_id in selected:
        if target_id not in targets:
            raise ValueError(f"unknown target ID: {target_id}")
        target = targets[target_id]
        if target.get("mode") != "multi-node":
            raise ValueError(f"target {target_id} must have mode multi-node")
        recipe = target.get("recipe")
        if not isinstance(recipe, str) or not re.fullmatch(r"models/en/.+\.ya?ml", recipe):
            raise ValueError(f"target {target_id} needs an English recipe path")
        if "\\" in recipe or ".." in PurePosixPath(recipe).parts:
            raise ValueError(f"invalid recipe path: {recipe}")
        path = (root / recipe).resolve()
        if not path.is_relative_to(root / "models/en") or not path.is_file():
            raise ValueError(f"recipe not found or outside models/en: {recipe}")
        test_id = target.get("test_id")
        if not isinstance(test_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", test_id):
            raise ValueError(f"target {target_id} needs a valid test_id")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"recipe must be a mapping: {recipe}")
        # Current converter recipes keep their tests in scenarios. An explicit
        # multi_node_tests list, when present, is authoritative.
        multi_node_tests = value.get("multi_node_tests", value.get("scenarios"))
        if not isinstance(multi_node_tests, list) or any(not isinstance(test, dict) for test in multi_node_tests):
            raise ValueError(f"recipe multi_node_tests/scenarios must be a list of mappings: {recipe}")
        matches = [test for test in multi_node_tests if test.get("test_id") == test_id]
        if len(matches) != 1:
            raise ValueError(f"recipe {recipe} must contain exactly one test_id {test_id!r}")
        include.append({"target_id": target_id, "recipe": recipe,
                        "test_id": test_id, "name": target_id})
    return {"include": include}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--trigger", choices=("nightly", "manual"), required=True)
    parser.add_argument("--target-ids", default="", help="Comma-separated manual target override")
    args = parser.parse_args()
    try:
        plan = build_plan(args.root, args.trigger, args.target_ids)
    except (ValueError, OSError, yaml.YAMLError) as error:
        parser.exit(2, f"multi-node plan: {error}\n")
    print(json.dumps(plan, separators=(",", ":")))


if __name__ == "__main__":
    main()
