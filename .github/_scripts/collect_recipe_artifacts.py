"""Keep the publisher's single-artifact contract for multi-hardware runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def collect(input_dir: Path, output_dir: Path, *, expected_plan: dict | None = None) -> list[str]:
    groups: dict[str, list[tuple[Path, dict, str]]] = {}
    for path in sorted(input_dir.rglob("*.params.json")):
        slug = path.name.removesuffix(".params.json")
        params = json.loads(path.read_text(encoding="utf-8"))
        status_file = path.with_name(f"{slug}.status")
        status = status_file.read_text(encoding="utf-8").strip() if status_file.is_file() else "unknown"
        if status not in {"pass", "fail", "skip"}:
            status = "unknown"
        groups.setdefault(slug, []).append((path, params, status))

    # Parser/setup failures can precede params capture. The wrapper still
    # stamps .status/.recipe; those failures must not disappear in a merge.
    for path in sorted(input_dir.rglob("*.status")):
        slug = path.stem
        if path.with_name(f"{slug}.params.json").is_file():
            continue
        recipe_file = path.with_name(f"{slug}.recipe")
        recipe = recipe_file.read_text(encoding="utf-8").strip() if recipe_file.is_file() else ""
        state = path.read_text(encoding="utf-8").strip()
        state = state if state in {"fail", "skip"} else "unknown"
        groups.setdefault(slug, []).append((path, {"recipe_path": recipe, "scenarios": []}, state))

    # A selected job can fail before writing/uploading any artifact. Check the
    # original plan as well, so its missing evidence cannot become a sibling's
    # pass. Unknown means unverified, not proof of a model/runtime failure.
    for job in (expected_plan or {}).get("include", []):
        for recipe in json.loads(job["recipe_files"]):
            slug = Path(recipe).stem
            rows = groups.setdefault(slug, [])
            if any(data.get("recipe_path") == recipe
                   and (path.relative_to(input_dir).parts[0].endswith("-" + job["job_id"])
                        # download-artifact v7 flattens a single matching
                        # artifact even when merge-multiple is false.
                        or (path.parent == input_dir
                            and data.get("hw_key") == job["hardware_key"]
                            and data.get("image") == job["image"]))
                   for path, data, _ in rows):
                continue
            path = input_dir / ("missing-" + job["job_id"]) / f"{slug}.params.json"
            rows.append((path, {"recipe_path": recipe, "hw_key": job["hardware_key"],
                               "image": job["image"], "scenarios": [],
                               "missing_reason": "selected hardware job did not provide results"}, "unknown"))

    output_dir.mkdir(parents=True, exist_ok=True)
    for slug, rows in groups.items():
        recipes = {row[1].get("recipe_path") for row in rows if row[1].get("recipe_path")}
        if len(recipes) > 1:
            raise ValueError(f"different recipes share the artifact slug: {slug}")
        states = {row[2] for row in rows}
        status = next(s for s in ("fail", "unknown", "pass", "skip") if s in states)
        params = dict(rows[0][1])
        params["recipe_path"] = next(iter(recipes), "")
        if len(rows) > 1:
            # A recipe-level failure remains conservative under the existing
            # publisher: it must not turn a sibling hardware result green.
            params["hw_key"] = "mixed"
            params["image"] = "mixed; see hardware_runs"
            params["hardware_runs"] = [{**data, "status": state} for _, data, state in rows]
            params["scenarios"] = [s for _, data, state in rows if state in {"pass", "fail"}
                                   for s in data.get("scenarios", [])]
            if status != "skip":
                params.pop("skip_reason", None)
        (output_dir / f"{slug}.params.json").write_text(
            json.dumps(params, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        (output_dir / f"{slug}.status").write_text(status + "\n", encoding="utf-8")
        (output_dir / f"{slug}.result").write_text(status.upper() + "\n", encoding="utf-8")
        (output_dir / f"{slug}.recipe").write_text(params.get("recipe_path", "") + "\n", encoding="utf-8")
        benches = [path.with_name(f"{slug}.bench") for path, _, _ in rows]
        contents = [path.read_text(encoding="utf-8") for path in benches if path.is_file()]
        if contents:
            (output_dir / f"{slug}.bench").write_text("\n".join(contents), encoding="utf-8")
    return sorted(groups)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-plan", type=json.loads, default=None,
                        help="Original matrix JSON; missing selected jobs remain unverified")
    args = parser.parse_args()
    print(f"Collected {len(collect(args.input, args.output, expected_plan=args.expected_plan))} recipe result(s)")
