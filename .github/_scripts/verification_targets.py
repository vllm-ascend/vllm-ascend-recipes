#!/usr/bin/env python3
"""Load the explicit configuration-verification target registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_REQUIRED = {"id", "recipe", "mode", "runner", "selector"}
_SELECTOR_REQUIRED = {"npu", "precision", "deployment", "case"}


def load_targets(path: Path) -> list[dict[str, Any]]:
    """Return validated target entries from the repository registry."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("targets"), list):
        raise ValueError(f"{path}: expected a top-level targets list")

    targets: list[dict[str, Any]] = []
    ids: set[str] = set()
    recipes_and_test_ids: set[tuple[str, str]] = set()
    for index, target in enumerate(raw["targets"]):
        if not isinstance(target, dict):
            raise ValueError(f"{path}: target {index} must be a mapping")
        missing = _REQUIRED - target.keys()
        if missing:
            raise ValueError(f"{path}: target {index} is missing {sorted(missing)}")
        if not isinstance(target["id"], str) or not target["id"]:
            raise ValueError(f"{path}: target {index} has an invalid id")
        if target["id"] in ids:
            raise ValueError(f"{path}: duplicate target id {target['id']!r}")
        if not isinstance(target["selector"], dict):
            raise ValueError(f"{path}: target {target['id']!r} selector must be a mapping")
        selector_missing = _SELECTOR_REQUIRED - target["selector"].keys()
        if selector_missing:
            raise ValueError(
                f"{path}: target {target['id']!r} selector is missing {sorted(selector_missing)}"
            )
        if target["mode"] == "multi-node":
            test_id = target.get("test_id")
            if not isinstance(test_id, str) or not test_id:
                raise ValueError(f"{path}: multi-node target {target['id']!r} needs test_id")
            key = (target["recipe"], test_id)
            if key in recipes_and_test_ids:
                raise ValueError(f"{path}: duplicate recipe/test_id target {key!r}")
            recipes_and_test_ids.add(key)
        ids.add(target["id"])
        targets.append(target)
    return targets


def find_target(targets: list[dict[str, Any]], recipe: str, test_id: str) -> dict[str, Any] | None:
    """Find one multi-node target by its PR34 execution identity."""
    return next(
        (
            target
            for target in targets
            if target["recipe"] == recipe and target.get("test_id") == test_id
        ),
        None,
    )


def find_target_id(targets: list[dict[str, Any]], target_id: str) -> dict[str, Any] | None:
    """Find one target by its stable published-status key."""
    return next((target for target in targets if target["id"] == target_id), None)
