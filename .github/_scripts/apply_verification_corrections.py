"""Repair only CI outcomes whose original logs have been explicitly audited."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

FIELDS = ("npu", "precision", "deployment", "case")


def same_selector(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in FIELDS)


def apply_corrections(status_dir: Path, runs_dir: Path, audits: list[dict]) -> list[str]:
    changed = set()
    for audit in audits:
        for correction in audit["results"]:
            # Corrections invalidate audited false positives; they never invent passes.
            if correction["status"] not in {"fail", "unknown"}:
                raise ValueError("an audited correction must be fail or unknown")
            slug = Path(correction["recipe"]).stem
            path = status_dir / f"{slug}.json"
            if not path.is_file():
                continue
            status = json.loads(path.read_text(encoding="utf-8"))
            before = json.dumps(status, sort_keys=True)
            records = [status.get(f"last_{kind}_run") for kind in ("pr", "nightly", "manual")]
            target_records = []
            for target in status.get("targets", {}).values():
                entries = [target.get(f"last_{kind}_run") for kind in ("pr", "nightly", "manual")]
                entries += target.get("history", [])
                records.extend(entries)
                if same_selector(target.get("selector", {}), correction["selector"]):
                    target_records.extend(entries)
            matching = [record for record in records if isinstance(record, dict)
                        and record.get("workflow_run_id") == audit["workflow_run_id"]]
            heads = {record.get("head_sha", "") for record in matching}
            exact = [status.get(f"last_{kind}_run") for kind in ("pr", "nightly", "manual")]
            original_status = next((record.get("status") for record in exact
                                    if isinstance(record, dict)
                                    and record.get("workflow_run_id") == audit["workflow_run_id"]), None)
            for record in exact + target_records:
                if not isinstance(record, dict) or record.get("workflow_run_id") != audit["workflow_run_id"]:
                    continue
                record.update(status=correction["status"], verification_note=correction["reason"],
                              verification_evidence_url=audit["evidence_url"])
            if json.dumps(status, sort_keys=True) != before:
                path.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                changed.add(slug)
            for head in heads:
                if not isinstance(head, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", head):
                    continue
                params_path = runs_dir / head / f"{slug}.params.json"
                if not params_path.is_file():
                    continue
                params = json.loads(params_path.read_text(encoding="utf-8"))
                before_params = json.dumps(params, sort_keys=True)
                digest = hashlib.sha256(json.dumps(
                    params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()).hexdigest()
                # Archives are keyed by commit SHA, not run ID. A rerun can
                # replace them; only change the exact audited JSON snapshot.
                if digest != correction.get("params_sha256"):
                    continue
                # Legacy snapshots have no hardware result envelope. Preserve
                # their original result for siblings, rather than inheriting
                # the corrected model-wide failure on the next sync.
                if not isinstance(params.get("hardware_runs"), list) and original_status:
                    params.setdefault("status", original_status)
                for source in [params, *params.get("hardware_runs", [])]:
                    for scenario in source.get("scenarios", []):
                        if same_selector(scenario, correction["selector"]):
                            scenario.update(verification_status=correction["status"],
                                            verification_reason=correction["reason"],
                                            verification_evidence_url=audit["evidence_url"])
                if json.dumps(params, sort_keys=True) != before_params:
                    params_path.write_text(json.dumps(params, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    changed.add(slug)
    return sorted(changed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-dir", type=Path, default=Path("public/status"))
    parser.add_argument("--runs-dir", type=Path, default=Path("public/runs"))
    parser.add_argument("--audits", type=Path,
                        default=Path(__file__).resolve().parents[1] / "verification-corrections.json")
    args = parser.parse_args()
    audits = json.loads(args.audits.read_text(encoding="utf-8"))["audits"]
    updated = apply_corrections(args.status_dir, args.runs_dir, audits)
    print(f"Applied audited corrections to {len(updated)} model(s).")


if __name__ == "__main__":
    main()
