#!/usr/bin/env python3
"""Structured Recipe CI results and durable JSON file helpers."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


RESULT_SCHEMA_VERSION = "recipe-ci-result/v1"
NODE_RESULT_SCHEMA_VERSION = "recipe-ci-node-result/v1"
FAILURE_CATEGORIES = frozenset(
    {
        "validation_failed",
        "launch_failed",
        "startup_timeout",
        "readiness_failed",
        "gateway_failed",
        "node_failed",
        "check_failed",
        "evaluation_failed",
        "coordinator_unreachable",
        "cancelled",
        "cleanup_failed",
        "internal_error",
    }
)
FINAL_STATUSES = frozenset({"passed", "failed", "cancelled"})


@dataclass(frozen=True)
class RunFailure:
    """The single structured failure shape used across runner stages."""

    category: str
    message: str
    stage: str | None = None
    node_id: str | None = None
    step_id: str | None = None
    return_code: int | None = None
    log_path: str | None = None

    def __post_init__(self) -> None:
        if self.category not in FAILURE_CATEGORIES:
            raise ValueError(f"unknown failure category: {self.category}")
        if not self.message:
            raise ValueError("failure message must not be empty")
        if self.log_path is not None:
            _artifact_path(self.log_path, "failure.log_path")

    def to_dict(self) -> dict[str, Any]:
        values = {
            "category": self.category,
            "stage": self.stage,
            "node_id": self.node_id,
            "step_id": self.step_id,
            "message": self.message,
            "return_code": self.return_code,
            "log_path": self.log_path,
        }
        return {key: value for key, value in values.items() if value is not None}


def utc_now() -> str:
    """Return a seconds-precision UTC timestamp in the result schema format."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _artifact_path(value: str | Path, field: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be an artifact-relative path: {value}")
    return path.as_posix()


def process_record(
    *,
    name: str,
    pid: int,
    process_group: int,
    started_at: str,
    log_path: str | Path,
    stage: str | None = None,
    return_code: int | None = None,
) -> dict[str, Any]:
    """Build the stable process subset stored in a node result."""
    if pid <= 0 or process_group <= 0:
        raise ValueError("pid and process_group must be positive")
    record: dict[str, Any] = {
        "name": name,
        "pid": pid,
        "process_group": process_group,
        "started_at": started_at,
        "log_path": _artifact_path(log_path, "process.log_path"),
    }
    if stage is not None:
        record["stage"] = stage
    if return_code is not None:
        record["return_code"] = return_code
    return record


def _cleanup_failures(
    cleanup_errors: Iterable[RunFailure],
) -> list[RunFailure]:
    errors = list(cleanup_errors)
    for error in errors:
        if error.category != "cleanup_failed":
            raise ValueError("cleanup_errors must use category cleanup_failed")
    return errors


def _outcome(
    status: str,
    primary_failure: RunFailure | None,
    cleanup_errors: list[RunFailure],
) -> tuple[str, RunFailure | None]:
    if status not in FINAL_STATUSES:
        raise ValueError(f"unknown result status: {status}")

    # A cleanup failure becomes primary only when execution had no earlier error.
    # Otherwise the original failure remains primary and cleanup stays secondary.
    if primary_failure is None and cleanup_errors:
        return "failed", cleanup_errors[0]
    if status == "passed" and primary_failure is not None:
        raise ValueError("passed result must not contain a primary failure")
    if status != "passed" and primary_failure is None:
        raise ValueError(f"{status} result requires a primary failure")
    return status, primary_failure


def build_node_result(
    *,
    node_id: str,
    role: str,
    status: str,
    started_at: str,
    processes: Sequence[Mapping[str, Any]] = (),
    ready_at: str | None = None,
    terminal_at: str | None = None,
    cleaned_at: str | None = None,
    primary_failure: RunFailure | None = None,
    cleanup_errors: Iterable[RunFailure] = (),
    warnings: Iterable[str] = (),
    artifacts: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Build the per-node result written only after local cleanup."""
    cleanup = _cleanup_failures(cleanup_errors)
    status, primary_failure = _outcome(status, primary_failure, cleanup)
    cleaned_at = cleaned_at or utc_now()
    return {
        "schema_version": NODE_RESULT_SCHEMA_VERSION,
        "node_id": node_id,
        "role": role,
        "status": status,
        "processes": [dict(process) for process in processes],
        "ready_at": ready_at,
        "terminal_at": terminal_at,
        "cleaned_at": cleaned_at,
        "primary_failure": (
            primary_failure.to_dict() if primary_failure is not None else None
        ),
        "cleanup_errors": [error.to_dict() for error in cleanup],
        "warnings": list(warnings),
        "artifacts": [
            _artifact_path(path, "artifacts[]") for path in artifacts
        ],
        "started_at": started_at,
    }


def build_final_result(
    *,
    plan: str,
    status: str,
    started_at: str,
    nodes: Mapping[str, Mapping[str, Any]] | None = None,
    checks: Mapping[str, Any] | None = None,
    evaluations: Mapping[str, Any] | None = None,
    primary_failure: RunFailure | None = None,
    cleanup_errors: Iterable[RunFailure] = (),
    warnings: Iterable[str] = (),
    finished_at: str | None = None,
) -> dict[str, Any]:
    """Build the leader summary from coordinator-visible structured state."""
    cleanup = _cleanup_failures(cleanup_errors)
    status, primary_failure = _outcome(status, primary_failure, cleanup)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "plan": plan,
        "status": status,
        "failure": (
            primary_failure.to_dict() if primary_failure is not None else None
        ),
        "cleanup_errors": [error.to_dict() for error in cleanup],
        "nodes": {
            node_id: dict(result) for node_id, result in (nodes or {}).items()
        },
        "checks": dict(checks or {}),
        "evaluations": dict(evaluations or {}),
        "warnings": list(warnings),
        "started_at": started_at,
        "finished_at": finished_at or utc_now(),
    }


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    """Write a JSON object via fsynced temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(
            file_descriptor, "w", encoding="utf-8", newline="\n"
        ) as output:
            json.dump(
                value,
                output,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> dict[str, Any]:
    """Read a result or step-result JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value
