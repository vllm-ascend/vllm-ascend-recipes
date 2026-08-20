from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[3]
MULTI_NODE_ROOT = ROOT / "test/recipe/multi_node"
sys.path.insert(0, str(MULTI_NODE_ROOT))

from converter import (  # noqa: E402
    ConversionError,
    load_parameters,
    read_scenario,
    render_scenario,
)
from converter.cli import _load_aisbench_defaults, _parameter_digest, main  # noqa: E402
from converter.emitter import emit_bundle  # noqa: E402
from converter.planner import plan_scenario  # noqa: E402
from scripts.plan import load_plan  # noqa: E402


CASES = (
    (
        ROOT / "models/en/DeepSeek/template_pd.yaml",
        "pd-2n2c",
        "template-pd-pd-2n2c",
        "vllm-ascend/DeepSeek-V2-Lite-W8A8",
        "deepseek-v2-lite",
        ("prefill", "decode"),
        True,
    ),
    (
        ROOT / "models/en/Qwen/template2_non_pd.yaml",
        "dp-2n2c",
        "template2-non-pd-dp-2n2c",
        "Qwen/Qwen3-30B-A3B",
        "qwen3",
        ("api", "headless"),
        False,
    ),
    (
        ROOT / "models/en/DeepSeek/DeepSeek-V2-Lite-W8A8.yaml",
        "dsv2lite-pd-2n2c",
        "deepseek-v2-lite-w8a8-dsv2lite-pd-2n2c",
        "vllm-ascend/DeepSeek-V2-Lite-W8A8",
        "dsv2",
        ("prefill", "decode"),
        True,
    ),
    (
        ROOT / "models/en/Qwen/Qwen3-30B-A3B.yaml",
        "qwen30b-dp-2n2c",
        "qwen3-30b-a3b-qwen30b-dp-2n2c",
        "Qwen/Qwen3-30B-A3B",
        "qwen3",
        ("api", "headless"),
        False,
    ),
)


def make_bundle(recipe: Path, test_id: str, plan_name: str):
    """Exercise all pre-emission converter phases for one real Recipe."""
    source = read_scenario(recipe, test_id)
    parameters = load_parameters(source, [])
    rendered = render_scenario(source, parameters)
    return plan_scenario(
        rendered,
        plan_name,
        _parameter_digest(parameters),
        _load_aisbench_defaults(),
    )


class ConverterPipelineTests(unittest.TestCase):
    def test_real_recipes_generate_runtime_loadable_bundles(self) -> None:
        """Both supported topologies traverse reader through runtime loader."""
        for (
            recipe,
            test_id,
            plan_name,
            model_id,
            served_name,
            roles,
            has_gateway,
        ) in CASES:
            with self.subTest(test_id=test_id), tempfile.TemporaryDirectory() as tmp:
                generated_root = Path(tmp) / ".generated"
                output = generated_root / recipe.stem / test_id
                bundle = make_bundle(recipe, test_id, plan_name)

                with patch("converter.emitter._GENERATED_ROOT", generated_root):
                    emit_bundle(bundle, output)

                plan = load_plan(output / "plan.yaml")
                self.assertEqual(plan.name, plan_name)
                self.assertEqual(plan.model.id, model_id)
                self.assertEqual(plan.model.cache_path, model_id)
                self.assertEqual(plan.model.served_name, served_name)
                self.assertEqual(tuple(node.role for node in plan.nodes), roles)
                self.assertEqual(plan.gateway is not None, has_gateway)
                self.assertEqual(
                    tuple(stage.id for stage in plan.stages),
                    ("completion", "accuracy", "performance"),
                )

                raw_plan = yaml.safe_load(
                    (output / "plan.yaml").read_text(encoding="utf-8")
                )
                metadata = raw_plan["metadata"]
                self.assertEqual(metadata["test_id"], test_id)
                self.assertEqual(
                    metadata["source_recipe"], recipe.relative_to(ROOT).as_posix()
                )
                self.assertEqual(len(metadata["digests"]["recipe"]), 64)
                self.assertEqual(len(metadata["digests"]["parameters"]), 64)
                self.assertTrue((output / "checks/completion.sh").is_file())
                self.assertTrue((output / "evaluations/run_aisbench.sh").is_file())
                completion = (output / "checks/completion.sh").read_text(
                    encoding="utf-8"
                )
                self.assertIn(
                    r'-d "{\"model\":\"$MULTI_NODE_SERVED_MODEL_NAME\"',
                    completion,
                )
                self.assertIn(r"printf '%s\n'", completion)

    def test_generation_atomically_replaces_previous_output(self) -> None:
        recipe, test_id, plan_name, *_ = CASES[0]
        bundle = make_bundle(recipe, test_id, plan_name)
        with tempfile.TemporaryDirectory() as tmp:
            generated_root = Path(tmp) / ".generated"
            output = generated_root / recipe.stem / test_id
            with patch("converter.emitter._GENERATED_ROOT", generated_root):
                emit_bundle(bundle, output)
                readme = output / "README.md"
                readme.write_text("manual drift\n", encoding="utf-8")
                emit_bundle(bundle, output)
                self.assertNotEqual(readme.read_text(encoding="utf-8"), "manual drift\n")

    def test_cli_uses_default_output_with_parameter_override(self) -> None:
        recipe, test_id, plan_name, *_ = CASES[1]
        with tempfile.TemporaryDirectory() as tmp:
            generated_root = Path(tmp) / ".generated"
            output = generated_root / "template2-non-pd" / test_id
            arguments = [
                "--recipe",
                str(recipe),
                "--test-id",
                test_id,
                "--set",
                "max_num_seqs=16",
            ]
            stdout = io.StringIO()
            with (
                patch("converter.cli._GENERATED_ROOT", generated_root),
                patch("converter.emitter._GENERATED_ROOT", generated_root),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(main(arguments), 0)
            self.assertEqual(stdout.getvalue(), f"Generated {output.resolve()}\n")

            node_script = (output / "nodes/node0/run.sh").read_text(encoding="utf-8")
            self.assertIn("--max-num-seqs 16", node_script)

    def test_cli_rejects_recipe_outside_template_allowlist(self) -> None:
        recipe, test_id, plan_name, *_ = CASES[0]
        existing_recipe = ROOT / "models/en/DeepSeek/DeepSeek-V4-Flash.yaml"
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main(
                    [
                        "--recipe",
                        str(existing_recipe),
                        "--test-id",
                        test_id,
                        "--output",
                        str(Path(tmp) / plan_name),
                    ]
                )
        self.assertEqual(status, 2)
        self.assertIn("unsupported --recipe", stderr.getvalue())

    def test_cli_rejects_template_test_id_mismatch(self) -> None:
        recipe, _, plan_name, *_ = CASES[0]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = main(
                [
                    "--recipe",
                    str(recipe),
                    "--test-id",
                    "dp-2n2c",
                    "--output",
                    str(ROOT / "test/recipe/multi_node/.generated" / plan_name),
                ]
            )
        self.assertEqual(status, 2)
        self.assertIn("only supports --test-id 'pd-2n2c'", stderr.getvalue())

    def test_cli_rejects_output_outside_generated_tree(self) -> None:
        recipe, test_id, *_ = CASES[0]
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(stderr):
            status = main(
                [
                    "--recipe",
                    str(recipe),
                    "--test-id",
                    test_id,
                    "--output",
                    str(Path(tmp) / "outside"),
                ]
            )
        self.assertEqual(status, 2)
        self.assertIn("output must be inside", stderr.getvalue())


class ConverterValidationTests(unittest.TestCase):
    def test_recipe_defaults_follow_frontend_merge_and_set_priority(self) -> None:
        source = read_scenario(CASES[1][0], CASES[1][1])
        self.assertEqual(
            source.parameter_defaults,
            {
                "max_model_len": 4096,
                "max_num_seqs": 8,
                "gpu_memory_utilization": 0.9,
            },
        )

        source = replace(
            source,
            parameter_defaults={**source.parameter_defaults, "frontend_only": True},
        )
        parameters = load_parameters(source, ["max_num_seqs=16"])
        self.assertNotIn("frontend_only", parameters)
        self.assertEqual(parameters["max_num_seqs"], 16)

    def test_unknown_test_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConversionError, "was not found"):
            read_scenario(CASES[0][0], "missing-test")

    def test_missing_recipe_parameter_default_is_rejected(self) -> None:
        source = read_scenario(CASES[0][0], CASES[0][1])
        with self.assertRaisesRegex(ConversionError, "missing parameters"):
            render_scenario(replace(source, parameter_defaults={}), {})

    def test_unused_command_line_override_is_rejected(self) -> None:
        source = read_scenario(CASES[0][0], CASES[0][1])
        with self.assertRaisesRegex(ConversionError, "unused parameters: typo"):
            render_scenario(source, load_parameters(source, ["typo=1"]))

    def test_resource_mismatch_is_rejected_before_emission(self) -> None:
        recipe, test_id, plan_name, *_ = CASES[1]
        source = read_scenario(recipe, test_id)
        source = render_scenario(source, load_parameters(source, []))
        with self.assertRaisesRegex(
            ConversionError, "must equal npu_per_node"
        ):
            plan_scenario(
                replace(source, npu_per_node=4),
                plan_name,
                "0" * 64,
                _load_aisbench_defaults(),
            )

    def test_gateway_port_drift_is_rejected_before_emission(self) -> None:
        recipe, test_id, plan_name, *_ = CASES[0]
        source = read_scenario(recipe, test_id)
        source = render_scenario(source, load_parameters(source, []))
        scripts = dict(source.scripts)
        gateway = scripts["gateway-0"]
        scripts["gateway-0"] = replace(
            gateway,
            content=gateway.content.replace(
                "--prefiller-ports 7100 7101",
                "--prefiller-ports 7200 7101",
            ),
        )
        with self.assertRaisesRegex(ConversionError, "Prefill ports"):
            plan_scenario(
                replace(source, scripts=scripts),
                plan_name,
                "0" * 64,
                _load_aisbench_defaults(),
            )

    def test_duplicate_test_id_is_rejected_before_selection(self) -> None:
        document = {
            "scenarios": [
                {"test_id": "duplicate-test"},
                {"test_id": "duplicate-test"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            recipe = Path(tmp) / "recipe.yaml"
            recipe.write_text(
                yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ConversionError, "Duplicate test_id"):
                read_scenario(recipe, "duplicate-test")


if __name__ == "__main__":
    unittest.main()
