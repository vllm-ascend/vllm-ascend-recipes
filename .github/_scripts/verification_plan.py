"""Select changed PR recipes independently of the nightly list."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

NIGHTLY_LIST = ".github/nightly-recipes.yaml"
IMAGE_BASE = "swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:"
HARDWARE = {
    "a2": ("atlas_800_a2", r"\bA2\b", "linux-aarch64-a2b4-8"),
    "a3": ("atlas_800_a3", r"\bA3\b", "linux-aarch64-a3-800i-16-cn12-001"),
    "a5": ("ascend_950dt", r"\b950DT\b", "linux-aarch64-a5-8"),
}


def recipe_image(recipe: dict, family: str, trigger: str) -> str:
    """Honor a declared image; never silently test new recipes on old vLLM."""
    declared = recipe.get("model", {}).get("docker_image")
    image = declared.get(HARDWARE[family][0]) if isinstance(declared, dict) else declared
    if image:
        # Only known, same-tag mirrors. Digests and newer tags remain intact.
        public = "quay.io/ascend/vllm-ascend:"
        known = {"v0.23.0rc1", "v0.23.0", "v0.23.0-a3", "v0.23.0-a5"}
        if image.startswith(public) and image.removeprefix(public) in known:
            return IMAGE_BASE + image.removeprefix(public)
        return image
    version = "v0.23.0rc1" if trigger == "pr" and family == "a2" else "v0.23.0"
    return IMAGE_BASE + version + ("" if family == "a2" else f"-{family}")


def single_node_scenarios(recipe: dict, hardware_key: str) -> list[dict]:
    """Use the same hardware/deployment predicate for planning and execution."""
    if recipe.get("meta", {}).get("hardware", {}).get(hardware_key) == "unsupported":
        return []
    pattern = next(pattern for key, pattern, _ in HARDWARE.values() if key == hardware_key)

    def has_single_node_serve(scenario: dict) -> bool:
        blocks = [block for step in scenario.get("steps", [])
                  for block in re.findall(r"```(?:bash|shell)\s*\n(.*?)```", step.get("content", ""), re.DOTALL)
                  if "vllm serve" in block]
        # A misleading deployment label must not allocate a multi-node command
        # to a single runner. Unknown nnodes variables are not safe to assume 1.
        return bool(blocks) and all(
            count.strip("\"'") == "1" for block in blocks
            for count in re.findall(r"--nnodes(?:\s*=\s*|\s+)([^\s\\]+)", block)
        )

    return [
        scenario for scenario in recipe.get("scenarios", [])
        if scenario.get("deployment", "").strip().lower() == "single-node"
        and re.search(pattern, scenario.get("npu", ""), re.IGNORECASE)
        and has_single_node_serve(scenario)
    ]


def english_recipes(paths: list[str]) -> list[str]:
    return sorted({
        path.replace("models/zh/", "models/en/", 1)
        for path in paths
        if re.fullmatch(r"models/(?:en|zh)/.+\.ya?ml", path)
    })


def build_plan(root: Path, trigger: str, *, changed_files: list[str] | None = None,
               recipe_allowlist: list[str] | None = None, full_run: bool = False,
               hardware_filter: str = "all", images: dict[str, str] | None = None) -> dict:
    root = root.resolve()
    if trigger not in {"pr", "nightly", "manual"}:
        raise ValueError(f"unknown trigger: {trigger}")
    if hardware_filter not in {"all", *HARDWARE}:
        raise ValueError(f"unknown hardware filter: {hardware_filter}")
    if trigger != "manual" and (recipe_allowlist or full_run):
        raise ValueError("explicit recipe selection requires a manual run")

    if trigger == "pr":
        # New recipes are eligible immediately. Never read the nightly list or
        # the website's verification-target registry as a PR admission check.
        paths = english_recipes(changed_files or [])
        candidates = {family: paths for family in HARDWARE}
    elif trigger == "manual" and (recipe_allowlist or full_run):
        paths = recipe_allowlist or [p.relative_to(root).as_posix() for p in (root / "models/en").rglob("*")
                                    if p.suffix in {".yaml", ".yml"}]
        if any(not re.fullmatch(r"models/en/.+\.ya?ml", path) for path in paths):
            raise ValueError("manual recipe paths must be models/en/*.yaml or *.yml")
        candidates = {family: sorted(set(paths)) for family in HARDWARE}
    else:
        config = yaml.safe_load((root / NIGHTLY_LIST).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("nightly configuration must be a mapping")
        candidates = config.get("single_node", config)  # Accept the former single-node-only file.
        if not isinstance(candidates, dict) or set(candidates) - HARDWARE.keys():
            raise ValueError("nightly list must map a2/a3/a5 to recipe path lists")
        if any(not isinstance(paths, list) or any(not isinstance(p, (str, dict)) for p in paths)
               for paths in candidates.values()):
            raise ValueError("nightly recipes must be lists of paths")

    changed = set(english_recipes(changed_files or []))
    recipes, jobs = {}, []
    for family, (key, _, runner) in HARDWARE.items():
        image_groups: dict[str, list[str]] = {}
        for entry in candidates.get(family, []):
            if isinstance(entry, dict) and (set(entry) - {"recipe", "image"} or not isinstance(entry.get("recipe"), str)):
                raise ValueError("nightly entry must contain recipe and optional image")
            name = entry["recipe"] if isinstance(entry, dict) else entry
            if not re.fullmatch(r"models/en/.+\.ya?ml", name):
                raise ValueError(f"invalid recipe path: {name}")
            path = (root / name).resolve()
            if not path.is_relative_to(root / "models/en"):
                raise ValueError(f"recipe path escapes models/en: {name}")
            if not path.is_file():
                if trigger == "pr":  # Deleted recipes must not allocate runners.
                    continue
                raise ValueError(f"recipe not found: {name}")
            if name not in recipes:
                recipes[name] = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(recipes[name], dict):
                    raise ValueError(f"recipe must be a mapping: {name}")
            matches = single_node_scenarios(recipes[name], key)
            if not matches and trigger != "pr" and not (recipe_allowlist or full_run):
                raise ValueError(f"nightly recipe has no executable {family} single-node scenario: {name}")
            if not matches:
                continue
            if trigger == "nightly" and changed_files is not None and NIGHTLY_LIST not in changed_files and name not in changed:
                continue
            image = ((images or {}).get(family) or (entry.get("image") if isinstance(entry, dict) else None)
                     or recipe_image(recipes[name], family, trigger))
            if not isinstance(image, str) or re.search(r"\s", image) or not re.search(r"[:@]", image):
                raise ValueError(f"invalid or unpinned container image for {name}: {image!r}")
            image_groups.setdefault(image, []).append(name)
        if hardware_filter not in {"all", family}:
            continue
        for image, paths in sorted(image_groups.items()):
            job_id = f"{family}-{hashlib.sha256(image.encode()).hexdigest()[:10]}"
            jobs.append({"hardware": family, "hardware_key": key, "runner": runner, "job_id": job_id,
                         "recipe_files": json.dumps(sorted(set(paths)), separators=(",", ":")),
                         "mount_share": family == "a5", "image": image})
    if recipe_allowlist and not jobs:
        raise ValueError("selected recipes have no executable single-node scenarios for this hardware")
    return {"include": jobs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--trigger", choices=("pr", "nightly", "manual"), required=True)
    parser.add_argument("--base-ref", help="Git base ref for incremental PR/push selection")
    parser.add_argument("--recipe-allowlist", default="")
    parser.add_argument("--full-run", action="store_true")
    parser.add_argument("--hardware-filter", default="all")
    for family in HARDWARE:
        parser.add_argument(f"--{family}-image", default="")
    args = parser.parse_args()
    try:
        changed = None
        if args.base_ref:
            changed = subprocess.check_output(
                ["git", "diff", "--name-only", "--diff-filter=ACDMRT", args.base_ref, "HEAD", "--"],
                cwd=args.root, text=True, encoding="utf-8",
            ).splitlines()
        result = build_plan(args.root, args.trigger, changed_files=changed,
                            recipe_allowlist=[p.strip() for p in args.recipe_allowlist.split(",") if p.strip()],
                            full_run=args.full_run, hardware_filter=args.hardware_filter,
                            images={family: getattr(args, f"{family}_image") for family in HARDWARE})
    except (ValueError, OSError, yaml.YAMLError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"verification plan: {error}\n")
    print(json.dumps(result, separators=(",", ":")))
    for job in result["include"]:
        print(f"{job['hardware']}: {job['runner']} {job['recipe_files']}", file=sys.stderr)


if __name__ == "__main__":
    main()
