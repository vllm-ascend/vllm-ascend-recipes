"""Persist the outcome of one executed single-node scenario."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def record_result(path: Path, index: int, status: str, reason: str = "") -> None:
    if status not in {"pass", "fail", "skip", "unknown"}:
        raise ValueError(f"invalid scenario outcome: {status}")
    params = json.loads(path.read_text(encoding="utf-8"))
    matches = [scenario for scenario in params["scenarios"] if scenario.get("index") == index]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one scenario with index {index}")
    matches[0]["verification_status"] = status
    matches[0]["verification_reason"] = reason
    path.write_text(json.dumps(params, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path, required=True)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    record_result(args.params, args.index, args.status, args.reason)


if __name__ == "__main__":
    main()
