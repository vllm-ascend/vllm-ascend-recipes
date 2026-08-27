#!/usr/bin/env python3
"""Build the public status index without shell/JSON argument ambiguity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_index(status_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Return an index containing only valid status files with a model name."""
    models: list[dict[str, str]] = []
    for status_file in sorted(status_dir.glob("*.json")):
        if status_file.name == "index.json":
            continue
        try:
            payload = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"Skipping invalid status file {status_file}: {error}", file=sys.stderr)
            continue
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model, str) or not model:
            print(f"Skipping status file without model: {status_file}", file=sys.stderr)
            continue
        models.append({"slug": model})
    return {"models": models}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = build_index(args.status_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Built {len(result['models'])} status index entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
