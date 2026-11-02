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
    "a2": ("atlas_800_a2", r"\bA2\b"),
    "a3": ("atlas_800_a3", r"\bA3\b"),
    "a5": ("ascend_950dt", r"\b950DT\b"),
}

# hardware_key -> env_setup.container section keys that document the device
# mounts. Recipes spell the same family inconsistently: the short "A2",
# the display form "Atlas 800 A2", and the raw hardware key "atlas_800_a2".
CONTAINER_SECTIONS = {
    "atlas_800_a2": ("A2", "Atlas 800 A2", "atlas_800_a2"),
    "atlas_800_a3": ("A3", "Atlas 800 A3", "atlas_800_a3"),
    "ascend_950dt": ("A5", "Ascend 950DT", "ascend_950dt"),
}

# Required NPU count -> self-hosted runner label per family. A recipe is
# dispatched to the smallest runner that fits its declared device mounts,
# instead of always occupying the largest pool. The 0-card pools are CPU-only
# controllers and are never selected for `vllm serve` verification.
RUNNERS = {
    "a2": {
        0: "linux-aarch64-a2b4-0",
        2: "linux-aarch64-a2b4-2",
        4: "linux-aarch64-a2b4-4",
        8: "linux-aarch64-a2b4-8",
    },
    "a3": {
        2: "linux-aarch64-a3-800i-2-cn12-001",
        4: "linux-aarch64-a3-800i-4-cn12-001",
        8: "linux-aarch64-a3-800i-8-cn12-001",
        16: "linux-aarch64-a3-800i-16-cn12-001",
    },
    "a5": {
        0: "linux-aarch64-a5-0",
        2: "linux-aarch64-a5-2",
        4: "linux-aarch64-a5-4",
        8: "linux-aarch64-a5-8",
    },
}


def required_cards(recipe: dict, hardware_key: str) -> int:
    """Count the NPUs a recipe declares via its env_setup container mounts."""
    container = recipe.get("env_setup", {}).get("container", {})
    if not isinstance(container, dict):
        return 0
    for section in CONTAINER_SECTIONS.get(hardware_key, ()):
        block = container.get(section)
        if not isinstance(block, dict):
            continue
        content = block.get("content", "")
        if isinstance(content, str):
            return len(re.findall(r"--device[\s=]+/dev/davinci\d+", content))
    return 0


def pick_runner(family: str, cards: int) -> str:
    """Smallest runner that fits `cards`; fall back to the largest pool."""
    pools = sorted(RUNNERS[family].items())
    if cards <= 0:
        return pools[-1][1]
    for capacity, runner in pools:
        if capacity >= cards:
            return runner
    return pools[-1][1]


def recipe_image(recipe: dict, family: str, trigger: str) -> str:
    """Honor a declared image; never silently test new recipes on old vLLM."""
    declared = recipe.get("model", {}).get("docker_image")
    image = declared.get(HARDWARE[family][0]) if isinstance(declared, dict) else declared
    if image:
        return image
    version = "v0.23.0rc1" if trigger == "pr" and family == "a2" else "v0.23.0"
    return IMAGE_BASE + version + ("" if family == "a2" else f"-{family}")


def single_node_scenarios(recipe: dict, hardware_key: str) -> list[dict]:
    """Use the same hardware/deployment predicate for planning and execution."""
    if recipe.get("meta", {}).get("hardware", {}).get(hardware_key) == "unsupported":
        return []
    pattern = next(pattern for key, pattern in HARDWARE.values() if key == hardware_key)

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
    for family, (key, _) in HARDWARE.items():
        image_groups: dict[tuple[str, str], list[str]] = {}
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
            # Apply CI transport policy after all overrides, before grouping.
            # Preserve the repository, tag/digest and hardware-specific suffix.
            if image.startswith("quay.io/"):
                image = "quay.nju.edu.cn/" + image.removeprefix("quay.io/")
            runner = pick_runner(family, required_cards(recipes[name], key))
            image_groups.setdefault((image, runner), []).append(name)
        if hardware_filter not in {"all", family}:
            continue
        for (image, runner), paths in sorted(image_groups.items()):
            job_id = f"{family}-{hashlib.sha256((image + runner).encode()).hexdigest()[:10]}"
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
    parser.add_argument("--head-ref", help="Commit to compare against the base (PR head); defaults to HEAD")
    parser.add_argument("--recipe-allowlist", default="")
    parser.add_argument("--full-run", action="store_true")
    parser.add_argument("--hardware-filter", default="all")
    for family in HARDWARE:
        parser.add_argument(f"--{family}-image", default="")
    args = parser.parse_args()
    try:
        changed = None
        if args.base_ref:
            # On pull_request events the checkout is the merge ref (PR head +
            # the base tip at fetch time), which can be newer than the base sha
            # recorded in the event payload. Diffing base..HEAD would then
            # report unrelated base-branch commits as PR changes, so always
            # compare from the merge base to the requested head commit.
            head = args.head_ref or "HEAD"
            base = subprocess.check_output(
                ["git", "merge-base", args.base_ref, head],
                cwd=args.root, text=True, encoding="utf-8",
            ).strip()
            changed = subprocess.check_output(
                ["git", "diff", "--name-only", "--diff-filter=ACDMRT", base, head, "--"],
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
