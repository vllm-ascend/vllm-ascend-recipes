"""Read the scenario-local contract from a Recipe document."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import yaml

from .model import ConversionError, ParameterValue, ScenarioSource, ScriptSource


_CASE_PATTERN = re.compile(r"(?:[1-9]\d*)p(?:[1-9]\d*)d|(?:[1-9]\d*)-node")
_PD_CASE_LEGACY = re.compile(r"^(?:[1-9]\d*)[pP](?:[1-9]\d*)[dD]")
_TEST_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PARAMETER_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEPLOYMENTS = {"pd", "non-pd"}
_AISBENCH_MODES = {"accuracy", "performance"}


def _mapping(value: Any, field: str) -> dict[str, Any]:
    """Require a YAML mapping while retaining its precise source field."""
    if not isinstance(value, dict):
        raise ConversionError(f"{field} must be a mapping")
    return value


def _text(value: Any, field: str) -> str:
    """Require a non-empty string without normalizing its value."""
    if not isinstance(value, str) or not value.strip():
        raise ConversionError(f"{field} must be a non-empty string")
    return value


def _load_recipe(path: Path) -> dict[str, Any]:
    """Load a Recipe and normalize file and YAML parser failures."""
    if not path.is_file():
        raise ConversionError(f"Recipe file not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConversionError(f"Cannot read Recipe {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConversionError(f"Invalid YAML in Recipe {path}: {error}") from error
    return _mapping(value, f"Recipe {path}")


def _select_scenario(
    scenarios: Any, requested_test_id: str
) -> dict[str, Any]:
    """Select one exact test id after checking Recipe-local uniqueness."""
    if not isinstance(scenarios, list):
        raise ConversionError("scenarios must be a list")

    selected: dict[str, Any] | None = None
    locations: dict[str, int] = {}
    for index, value in enumerate(scenarios):
        scenario = _mapping(value, f"scenarios[{index}]")
        if "test_id" not in scenario:
            continue
        test_id = _text(scenario["test_id"], f"scenarios[{index}].test_id")
        if _TEST_ID_PATTERN.fullmatch(test_id) is None:
            raise ConversionError(
                f"scenarios[{index}].test_id must be lowercase kebab-case"
            )
        if test_id in locations:
            raise ConversionError(
                f"Duplicate test_id {test_id!r} in scenarios "
                f"{locations[test_id]} and {index}"
            )
        locations[test_id] = index
        if test_id == requested_test_id:
            selected = scenario

    if selected is None:
        raise ConversionError(f"Scenario test_id {requested_test_id!r} was not found")
    return selected


def _read_scripts(value: Any) -> dict[str, ScriptSource]:
    """Decode the named script mapping required by later converter phases."""
    raw_scripts = _mapping(value, "scenario.scripts")
    if not raw_scripts:
        raise ConversionError("scenario.scripts must not be empty")

    scripts: dict[str, ScriptSource] = {}
    for name, value in raw_scripts.items():
        script_name = _text(name, "scenario.scripts key")
        script = _mapping(value, f"scenario.scripts.{script_name}")
        missing = [field for field in ("language", "content") if field not in script]
        if missing:
            raise ConversionError(
                f"scenario.scripts.{script_name} is missing fields: "
                f"{', '.join(missing)}"
            )
        scripts[script_name] = ScriptSource(
            language=_text(
                script["language"], f"scenario.scripts.{script_name}.language"
            ),
            content=_text(
                script["content"], f"scenario.scripts.{script_name}.content"
            ),
        )
    return scripts


def _read_aisbench(value: Any) -> tuple[str, ...]:
    """Validate the ordered set of supported AISBench stages."""
    if not isinstance(value, list):
        raise ConversionError("scenario.aisbench must be a list")
    modes: list[str] = []
    for index, item in enumerate(value):
        mode = _text(item, f"scenario.aisbench[{index}]")
        if mode not in _AISBENCH_MODES:
            supported = ", ".join(sorted(_AISBENCH_MODES))
            raise ConversionError(
                f"scenario.aisbench[{index}] must be one of: {supported}"
            )
        if mode in modes:
            raise ConversionError(f"scenario.aisbench contains duplicate mode {mode!r}")
        modes.append(mode)
    return tuple(modes)


def _config_param_definitions(value: Any, field: str) -> dict[str, dict[str, Any]]:
    """Validate one optional frontend parameter-definition mapping."""
    if value is None:
        return {}
    raw = _mapping(value, field)
    definitions: dict[str, dict[str, Any]] = {}
    for name, definition in raw.items():
        parameter_name = _text(name, f"{field} key")
        if _PARAMETER_NAME_PATTERN.fullmatch(parameter_name) is None:
            raise ConversionError(
                f"{field}.{parameter_name} must use a placeholder-compatible name"
            )
        definitions[parameter_name] = _mapping(
            definition, f"{field}.{parameter_name}"
        )
    return definitions


def _parameter_defaults(
    recipe_value: Any, scenario_value: Any
) -> dict[str, ParameterValue]:
    """Merge frontend definitions and retain only renderable scalar defaults."""
    definitions = _config_param_definitions(recipe_value, "config_params")
    definitions.update(
        _config_param_definitions(scenario_value, "scenario.config_params")
    )
    defaults: dict[str, ParameterValue] = {}
    for name, definition in definitions.items():
        if "default" not in definition:
            continue
        value = definition["default"]
        if not isinstance(value, (str, int, float, bool)):
            raise ConversionError(
                f"Effective config_params.{name}.default must be a non-null scalar"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ConversionError(
                f"Effective config_params.{name}.default must be finite"
            )
        defaults[name] = value
    return defaults


def read_scenario(path: Path, test_id: str) -> ScenarioSource:
    """Read one scenario and its frontend-compatible parameter defaults.

    Top-level model metadata, variants, and precision intentionally have no role
    in this phase. Legacy display-only scenarios may omit ``test_id`` and are
    skipped during exact selection.
    """
    requested_test_id = _text(test_id, "test_id")
    if _TEST_ID_PATTERN.fullmatch(requested_test_id) is None:
        raise ConversionError("test_id must be lowercase kebab-case")
    recipe_path = path.resolve()
    recipe = _load_recipe(recipe_path)
    if "scenarios" not in recipe:
        raise ConversionError("Recipe is missing scenarios")
    scenario = _select_scenario(recipe["scenarios"], requested_test_id)

    required = (
        "test_id",
        "npu",
        "deployment",
        "case",
        "npu_per_node",
        "scripts",
    )
    missing = [field for field in required if field not in scenario]
    if missing:
        raise ConversionError(
            f"Scenario {requested_test_id!r} is missing fields: {', '.join(missing)}"
        )

    deployment = _text(scenario["deployment"], "scenario.deployment")
    d = deployment.strip().lower()

    case = _text(scenario["case"], "scenario.case")
    is_pd = d == "pd" or (d != "non-pd" and "pd" in d)
    if d not in _DEPLOYMENTS and not is_pd:
        # Non-PD legacy display values (e.g. "Multi-Node") are accepted when
        # the case still matches the canonical <N>-node form.
        if _CASE_PATTERN.fullmatch(case) is None:
            raise ConversionError(
                "scenario.deployment must be 'pd', 'non-pd', or a legacy value "
                "with PD semantics (e.g. 'Multi-Node PD Separation'); non-PD "
                "legacy values require a '<positive integer>-node' case"
            )
    if is_pd:
        case_ok = (
            _CASE_PATTERN.fullmatch(case) is not None
            or _PD_CASE_LEGACY.match(case) is not None
        )
    else:
        case_ok = _CASE_PATTERN.fullmatch(case) is not None
    if not case_ok:
        raise ConversionError(
            "scenario.case must be '<positive integer>p<positive integer>d' "
            "(or a legacy form like '1P1D (1 Prefill node + 1 Decode node)'), "
            "or '<positive integer>-node'"
        )

    npu_per_node = scenario["npu_per_node"]
    if type(npu_per_node) is not int or npu_per_node <= 0:
        raise ConversionError("scenario.npu_per_node must be a positive integer")

    return ScenarioSource(
        recipe_path=recipe_path,
        test_id=requested_test_id,
        npu=_text(scenario["npu"], "scenario.npu"),
        deployment=deployment,
        case=case,
        npu_per_node=npu_per_node,
        aisbench=_read_aisbench(scenario.get("aisbench", [])),
        parameter_defaults=_parameter_defaults(
            recipe.get("config_params"), scenario.get("config_params")
        ),
        scripts=_read_scripts(scenario["scripts"]),
    )
