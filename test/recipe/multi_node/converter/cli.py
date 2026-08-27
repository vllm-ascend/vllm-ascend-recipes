"""Command-line entry point for deterministic Recipe-to-plan conversion."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Sequence

import yaml

from .emitter import EmitError, emit_bundle
from .model import ConversionError
from .parameters import load_parameters, render_scenario
from .planner import plan_scenario
from .reader import read_scenario
from .shell import ShellAnalysisError


_DEFAULTS_PATH = Path(__file__).with_name("aisbench_defaults.yaml")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_MULTI_NODE_ROOT = Path(__file__).resolve().parents[1]
_GENERATED_ROOT = _MULTI_NODE_ROOT / ".generated"
_SUPPORTED_CASES = {
    (
        _REPOSITORY_ROOT / "models/en/DeepSeek/template_pd.yaml"
    ).resolve(): {"pd-2n2c"},
    (
        _REPOSITORY_ROOT / "models/en/DeepSeek/DeepSeek-V2-Lite-W8A8.yaml"
    ).resolve(): {"dsv2lite-pd-2n2c"},
    (
        _REPOSITORY_ROOT / "models/en/Qwen/Qwen3-30B-A3B.yaml"
    ).resolve(): {"qwen30b-dp-2n2c"},
    (
        _REPOSITORY_ROOT / "models/en/Qwen/template2_non_pd.yaml"
    ).resolve(): {"dp-2n2c"},
    (
        _REPOSITORY_ROOT / "models/en/THUDM/GLM-5.yaml"
    ).resolve(): {"glm5-pd-cluster", "glm5-dp-2n-a3", "glm5-dp-2n-a2"},
}


def _parser() -> argparse.ArgumentParser:
    """Describe the intentionally small v1 converter interface."""
    parser = argparse.ArgumentParser(
        prog="python -m converter",
        description="Convert one Recipe test scenario into a multi-node plan bundle.",
    )
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--test-id", required=True)
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override one scalar parameter; may be repeated",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "generated bundle directory; defaults to "
            "test/recipe/multi_node/.generated/<recipe>/<test-id>"
        ),
    )
    return parser


def _parameter_digest(parameters: dict[str, object]) -> str:
    """Hash the resolved scalar parameter mapping independently of YAML style."""
    payload = json.dumps(
        parameters,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_aisbench_defaults() -> dict[str, object]:
    """Load the converter-owned common AISBench stage inputs."""
    try:
        value = yaml.safe_load(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConversionError(
            f"Cannot load AISBench defaults {_DEFAULTS_PATH}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise ConversionError("AISBench defaults must be a mapping")
    return value


def _source_recipe(path: Path, test_id: str) -> Path:
    """Accept only the two isolated multi-node template contracts."""
    recipe = path.expanduser().resolve()
    expected_test_ids = _SUPPORTED_CASES.get(recipe)
    if expected_test_ids is None:
        supported = ", ".join(
            path.relative_to(_REPOSITORY_ROOT).as_posix()
            for path in _SUPPORTED_CASES
        )
        raise ConversionError(
            f"unsupported --recipe {recipe}; supported templates: {supported}"
        )
    if test_id not in expected_test_ids:
        raise ConversionError(
            f"template {recipe.name} supports --test-id {sorted(expected_test_ids)!r}"
        )
    return recipe


def _normalized_recipe_stem(recipe: Path) -> str:
    """Convert a template filename into a stable kebab-case directory name."""
    value = re.sub(r"[^a-z0-9]+", "-", recipe.stem.lower()).strip("-")
    if not value:
        raise ConversionError(f"cannot derive an output name from {recipe.name}")
    return value


def default_output_path(recipe: Path, test_id: str) -> Path:
    """Return the deterministic ignored output directory for one template case."""
    source = _source_recipe(recipe, test_id)
    return _GENERATED_ROOT / _normalized_recipe_stem(source) / test_id


def convert(args: argparse.Namespace) -> None:
    """Run the ordered read, render, plan, and safe-emission pipeline."""
    recipe = _source_recipe(args.recipe, args.test_id)
    output = (
        args.output.expanduser()
        if args.output is not None
        else default_output_path(recipe, args.test_id)
    )
    source = read_scenario(recipe, args.test_id)
    parameters = load_parameters(source, args.overrides)
    rendered = render_scenario(source, parameters)
    bundle_name = f"{_normalized_recipe_stem(recipe)}-{args.test_id}"
    bundle = plan_scenario(
        rendered,
        bundle_name,
        _parameter_digest(parameters),
        _load_aisbench_defaults(),
    )
    emit_bundle(bundle, output)
    args.resolved_output = output.resolve(strict=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Return a shell-friendly status while keeping tracebacks for defects only."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        convert(args)
    except (ConversionError, EmitError, ShellAnalysisError) as error:
        print(f"converter: error: {error}", file=sys.stderr)
        return 2
    print(f"Generated {args.resolved_output}")
    return 0


__all__ = ["convert", "default_output_path", "main"]
