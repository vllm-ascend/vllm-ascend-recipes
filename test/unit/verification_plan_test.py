"""PR discovery has no model admission list; only nightly has a fixed scope."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github" / "_scripts"))
from verification_plan import build_plan
from collect_recipe_artifacts import collect


def scenario(npu="Ascend 950DT", deployment="Single-Node"):
    return {
        "npu": npu, "deployment": deployment, "precision": "W8A8", "case": "Smoke",
        "steps": [{"content": "```bash\nvllm serve your_model_path --port 8123\n```"}],
    }


class VerificationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write(".github/verification-targets.yaml", {"targets": []})
        self.write(".github/nightly-recipes.yaml", {"a2": [], "a3": [], "a5": []})

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def recipe(self, name="models/en/New/model.yaml", scenarios=None, hardware=None):
        self.write(name, {
            "model": {"model_id": "Example/model"},
            "meta": {"hardware": hardware or {"ascend_950dt": "experimental"}},
            "scenarios": scenarios if scenarios is not None else [scenario()],
        })
        return name

    def test_new_unregistered_recipe_is_scheduled_on_its_hardware(self):
        path = self.recipe()
        jobs = build_plan(self.root, "pr", changed_files=[path])["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["runner"], "linux-aarch64-a5-8")
        self.assertEqual(jobs[0]["hardware_key"], "ascend_950dt")
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])
        self.assertTrue(jobs[0]["mount_share"])

    def test_only_hardware_with_executable_single_node_scenarios_runs(self):
        path = self.recipe(scenarios=[scenario("Atlas 800I A2"), scenario(),
                                      scenario("Atlas 800I A3", "Multi-Node")])
        jobs = build_plan(self.root, "pr", changed_files=[path])["include"]
        self.assertEqual([j["hardware_key"] for j in jobs], ["atlas_800_a2", "ascend_950dt"])

    def test_chinese_and_english_edits_are_deduplicated(self):
        path = self.recipe()
        jobs = build_plan(self.root, "pr", changed_files=[path, path.replace("/en/", "/zh/"), path])["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_only_chinese_edit_verifies_its_english_recipe(self):
        path = self.recipe()
        jobs = build_plan(self.root, "pr", changed_files=[path.replace("/en/", "/zh/")])["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_unrelated_deleted_and_multi_node_files_do_not_allocate_runners(self):
        path = self.recipe(scenarios=[scenario(deployment="Multi-Node PD Separation")])
        plan = build_plan(self.root, "pr", changed_files=[path, "README.md", "models/en/Deleted.yaml"])
        self.assertEqual(plan, {"include": []})

    def test_mislabeled_single_node_command_cannot_start_a_two_node_job(self):
        item = scenario()
        item["steps"][0]["content"] = "```bash\nvllm serve your_model_path --nnodes 2\n```"
        path = self.recipe(scenarios=[item])
        self.assertEqual(build_plan(self.root, "pr", changed_files=[path]), {"include": []})

    def test_documentation_output_before_serve_does_not_hide_the_scenario(self):
        item = scenario()
        item["steps"][0]["content"] = "```bash\nINFO: Example output\n```\n" + item["steps"][0]["content"]
        path = self.recipe(scenarios=[item])
        self.assertEqual(len(build_plan(self.root, "pr", changed_files=[path])["include"]), 1)

    def test_explicitly_unsupported_hardware_is_not_allocated(self):
        path = self.recipe(hardware={"ascend_950dt": "unsupported"})
        self.assertEqual(build_plan(self.root, "pr", changed_files=[path]), {"include": []})

    def test_two_recipes_on_same_hardware_share_one_job(self):
        a, b = self.recipe(), self.recipe("models/en/Other/model.yaml")
        jobs = build_plan(self.root, "pr", changed_files=[b, a])["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [a, b])

    def test_recipe_declared_new_image_is_not_silently_downgraded(self):
        path = self.recipe()
        value = yaml.safe_load((self.root / path).read_text(encoding="utf-8"))
        value["model"]["docker_image"] = {"ascend_950dt": "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5"}
        self.write(path, value)
        jobs = build_plan(self.root, "pr", changed_files=[path])["include"]
        self.assertEqual(jobs[0]["image"], "quay.nju.edu.cn/ascend/vllm-ascend:v0.30.0rc2-a5")

    def test_quay_mirror_preserves_tags_and_digests_for_every_hardware(self):
        for family, key, npu, suffix in [
            ("a2", "atlas_800_a2", "Atlas 800I A2", ""),
            ("a3", "atlas_800_a3", "Atlas 800I A3", "-a3"),
            ("a5", "ascend_950dt", "Ascend 950DT", "-a5"),
        ]:
            for reference in (":v0.23.0" + suffix, ":v0.30.0rc2" + suffix,
                              "@sha256:" + "a" * 64):
                with self.subTest(family=family, reference=reference):
                    path = self.recipe(scenarios=[scenario(npu)], hardware={key: "verified"})
                    value = yaml.safe_load((self.root / path).read_text(encoding="utf-8"))
                    value["model"]["docker_image"] = {key: "quay.io/ascend/vllm-ascend" + reference}
                    self.write(path, value)
                    jobs = build_plan(self.root, "pr", changed_files=[path])["include"]
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(jobs[0]["hardware"], family)
                    self.assertEqual(jobs[0]["image"], "quay.nju.edu.cn/ascend/vllm-ascend" + reference)
                    self.assertEqual(yaml.safe_load((self.root / path).read_text())["model"], value["model"])

    def test_nightly_and_manual_image_overrides_use_the_same_quay_mirror(self):
        path = self.recipe()
        upstream = "quay.io/other/project:v1"
        self.write(".github/nightly-recipes.yaml", {"a5": [{"recipe": path, "image": upstream}]})
        for trigger, overrides in [("nightly", {}), ("manual", {"a5": upstream})]:
            with self.subTest(trigger=trigger):
                jobs = build_plan(self.root, trigger, images=overrides)["include"]
                self.assertEqual(jobs[0]["image"], "quay.nju.edu.cn/other/project:v1")

    def test_non_quay_images_are_not_rewritten(self):
        path = self.recipe()
        for image in ("swr.example/ascend:v1", "ghcr.io/org/project:v1", "docker.io/org/project:v1",
                      "quay.nju.edu.cn/org/project:v1", "quay.io.example/org/project:v1",
                      "example.com/quay.io/project:v1"):
            with self.subTest(image=image):
                self.write(".github/nightly-recipes.yaml", {"a5": [{"recipe": path, "image": image}]})
                jobs = build_plan(self.root, "nightly")["include"]
                self.assertEqual(jobs[0]["image"], image)

    def test_quay_and_nju_references_group_into_the_same_hardware_image_job(self):
        a, b = self.recipe(), self.recipe("models/en/Other/model.yaml")
        for path, registry in [(a, "quay.io"), (b, "quay.nju.edu.cn")]:
            value = yaml.safe_load((self.root / path).read_text(encoding="utf-8"))
            value["model"]["docker_image"] = registry + "/ascend/vllm-ascend:v0.30.0rc2-a5"
            self.write(path, value)
        jobs = build_plan(self.root, "pr", changed_files=[a, b])["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [a, b])
        self.assertEqual(jobs[0]["image"], "quay.nju.edu.cn/ascend/vllm-ascend:v0.30.0rc2-a5")

    def test_different_recipe_images_on_same_hardware_have_independent_jobs(self):
        a, b = self.recipe(), self.recipe("models/en/Other/model.yaml")
        value = yaml.safe_load((self.root / b).read_text(encoding="utf-8"))
        value["model"]["docker_image"] = "example.invalid/ascend:other-a5"
        self.write(b, value)
        jobs = build_plan(self.root, "pr", changed_files=[a, b])["include"]
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len({j["job_id"] for j in jobs}), 2)

    def test_unified_nightly_config_ignores_multi_node_for_single_node_planning(self):
        path = self.recipe()
        self.write(".github/nightly-recipes.yaml", {"single_node": {"a5": [path]}, "multi_node": ["example-multi"]})
        jobs = build_plan(self.root, "nightly")["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_a3_can_run_a_sixteen_card_single_node_recipe(self):
        item = scenario("Atlas 800I A3")
        item["steps"][0]["content"] = "```bash\nvllm serve your_model_path --tensor-parallel-size 16\n```"
        path = self.recipe(scenarios=[item])
        jobs = build_plan(self.root, "pr", changed_files=[path])["include"]
        self.assertEqual(jobs[0]["runner"], "linux-aarch64-a3-800i-16-cn12-001")

    def test_env_setup_device_count_selects_the_small_fitting_runner(self):
        path = self.recipe(scenarios=[scenario("Atlas 800I A2")])
        value = yaml.safe_load((self.root / path).read_text(encoding="utf-8"))
        value["env_setup"] = {"container": {"A2": {"content":
            "docker run --device /dev/davinci0 --device /dev/davinci_manager -it image bash"}}}
        self.write(path, value)
        jobs = build_plan(self.root, "pr", changed_files=[path])["include"]
        self.assertEqual(jobs[0]["runner"], "linux-aarch64-a2b4-2")

    def test_different_device_counts_do_not_share_a_runner_job(self):
        one = self.recipe("models/en/New/one.yaml", scenarios=[scenario("Atlas 800I A2")])
        eight = self.recipe("models/en/New/eight.yaml", scenarios=[scenario("Atlas 800I A2")])
        for path, devices in ((one, 1), (eight, 8)):
            value = yaml.safe_load((self.root / path).read_text(encoding="utf-8"))
            value["env_setup"] = {"container": {"A2": {"content":
                "docker run " + " ".join(f"--device /dev/davinci{i}" for i in range(devices))
                + " -it image bash"}}}
            self.write(path, value)
        jobs = build_plan(self.root, "pr", changed_files=[one, eight])["include"]
        self.assertEqual({j["runner"] for j in jobs}, {"linux-aarch64-a2b4-2", "linux-aarch64-a2b4-8"})

    def test_env_setup_section_key_spellings_are_all_recognized(self):
        paths = []
        for i, section_key in enumerate(("A2", "Atlas 800 A2", "atlas_800_a2")):
            path = self.recipe(f"models/en/New/model{i}.yaml", scenarios=[scenario("Atlas 800I A2")])
            value = yaml.safe_load((self.root / path).read_text(encoding="utf-8"))
            value["env_setup"] = {"container": {section_key: {"content":
                "docker run --device /dev/davinci0 --device /dev/davinci1 -it image bash"}}}
            self.write(path, value)
            paths.append(path)
        jobs = build_plan(self.root, "pr", changed_files=paths)["include"]
        self.assertEqual({j["runner"] for j in jobs}, {"linux-aarch64-a2b4-2"})

    def test_shell_comments_with_inline_backticks_do_not_hide_real_serve_command(self):
        path = "models/en/MoonshotAI/Kimi-K2.6.yaml"
        jobs = build_plan(ROOT, "pr", changed_files=[path])["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["hardware_key"], "atlas_800_a3")
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_manual_full_run_discovers_yml_recipes_too(self):
        path = self.recipe("models/en/New/model.yml")
        jobs = build_plan(self.root, "manual", full_run=True)["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_pr_does_not_require_a_nightly_or_page_registry(self):
        (self.root / ".github/nightly-recipes.yaml").unlink()
        (self.root / ".github/verification-targets.yaml").unlink()
        path = self.recipe()
        self.assertEqual(len(build_plan(self.root, "pr", changed_files=[path])["include"]), 1)

    def test_nightly_push_only_rechecks_changed_listed_recipes(self):
        a, b = self.recipe(), self.recipe("models/en/Other/model.yaml")
        self.write(".github/nightly-recipes.yaml", {"a5": [a, b]})
        jobs = build_plan(self.root, "nightly", changed_files=[b])["include"]
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [b])

    def test_nightly_list_edit_checks_the_whole_list(self):
        path = self.recipe()
        self.write(".github/nightly-recipes.yaml", {"a5": [path]})
        jobs = build_plan(self.root, "nightly", changed_files=[".github/nightly-recipes.yaml"])["include"]
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_manual_selection_can_run_a_recipe_not_in_nightly(self):
        path = self.recipe()
        jobs = build_plan(self.root, "manual", recipe_allowlist=[path], hardware_filter="a5")["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [path])

    def test_invalid_explicit_nightly_recipe_fails_instead_of_silently_skipping(self):
        path = self.recipe(scenarios=[scenario(deployment="Multi-Node")])
        self.write(".github/nightly-recipes.yaml", {"a5": [path]})
        with self.assertRaisesRegex(ValueError, "no executable"):
            build_plan(self.root, "nightly")

    def test_manual_selection_with_no_matching_hardware_fails(self):
        path = self.recipe()
        with self.assertRaisesRegex(ValueError, "no executable"):
            build_plan(self.root, "manual", recipe_allowlist=[path], hardware_filter="a2")

    def test_nightly_uses_only_its_fixed_hardware_recipe_pairs(self):
        a = self.recipe(scenarios=[scenario("Atlas 800I A2"), scenario()])
        self.recipe("models/en/Other/model.yaml")
        self.write(".github/nightly-recipes.yaml", {"a2": [a], "a3": [], "a5": []})
        jobs = build_plan(self.root, "nightly")["include"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["hardware_key"], "atlas_800_a2")
        self.assertEqual(json.loads(jobs[0]["recipe_files"]), [a])


class RecipeParserTests(unittest.TestCase):
    def parse(self, scenarios, hardware="ascend_950dt", missing_weights=False, *, alias=True, standard_cache=False,
              verification="", tasks=None):
        script = (ROOT / "scripts/verify-recipe.sh").read_text(encoding="utf-8")
        parser = script.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "recipe.yaml"
            recipe.write_text(yaml.safe_dump({"model": {"model_id": "Example/model"},
                                             "meta": {"tasks": tasks or ["text-generation"]},
                                             "verification": verification, "scenarios": scenarios}), encoding="utf-8")
            cache = root / "weights"
            cache.mkdir()
            if not missing_weights:
                weights = cache / ("Example/model" if standard_cache else "model")
                weights.mkdir(parents=True)
                (weights / "config.json").write_text('{}', encoding="utf-8")
            aliases = root / "aliases.yaml"
            aliases.write_text(yaml.safe_dump({"aliases": [{"model_id": "Example/model", "cache_dir": "model"}] if alias else []}), encoding="utf-8")
            env = {**os.environ, "PYTHONUTF8": "1", "RECIPE_REPO_ROOT": str(ROOT),
                   "RECIPE_CACHE_BASE": str(cache)}
            env.pop("RECIPE_TARGET_ID", None)
            result = subprocess.run([sys.executable, "-", str(recipe), hardware, str(aliases)],
                                    input=parser, capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_parser_keeps_single_node_and_excludes_same_hardware_multi_node(self):
        result = self.parse([scenario(), scenario(deployment="Multi-Node")])
        self.assertEqual(result["action"], "verify")
        self.assertEqual([s["deployment"] for s in result["scenarios"]], ["Single-Node"])

    def test_missing_weights_remains_explicitly_skipped(self):
        result = self.parse([scenario()], missing_weights=True)
        self.assertEqual(result["action"], "skip")
        self.assertIn("镜像未预装", result["reason"])

    def test_standard_preloaded_weights_do_not_require_an_alias_pr(self):
        result = self.parse([scenario()], alias=False, standard_cache=True)
        self.assertEqual(result["action"], "verify")
        self.assertIn("Example/model", result["cache_path"].replace("\\", "/"))

    def test_model_path_alias_uses_the_resolved_offline_directory(self):
        item = scenario()
        item["steps"][0]["content"] = (
            "```bash\nexport MODEL_PATH=model\n"
            "vllm serve $MODEL_PATH --served-model-name model --port 8123\n```"
        )
        result = self.parse([item])
        command = result["scenarios"][0]["serve_cmd"]
        self.assertIn("MODEL_PATH=" + shlex.quote(result["cache_path"]), command)
        self.assertIn("--served-model-name model", command)

    def test_literal_cache_alias_is_resolved_without_rewriting_request_names(self):
        item = scenario()
        item["steps"][0]["content"] = (
            "```bash\nvllm serve model --served-model-name model --port 8123\n```"
        )
        result = self.parse([item])
        command = result["scenarios"][0]["serve_cmd"]
        self.assertIn("vllm serve " + shlex.quote(result["cache_path"]), command)
        self.assertIn("--served-model-name model", command)

    def test_log_example_does_not_hide_later_global_request(self):
        verification = "```bash\nINFO: Server ready\n```\n```bash\ncurl http://<node_ip>:<port>/v1/embeddings\n```"
        result = self.parse([scenario()], verification=verification, tasks=["embedding"])
        checks = result["scenarios"][0]["verify_cmds"]
        self.assertEqual(checks, ["curl http://localhost:8123/v1/embeddings"])
        self.assertFalse(result["scenarios"][0]["benchmark_chat"])

    def test_scenario_task_check_overrides_global_request_for_another_variant(self):
        item = scenario()
        item["steps"].append({"content": "```bash\ncurl http://<server_ip>:<port>/v1/chat/completions -d '{\"model\":\"a5-model\"}'\n```"})
        global_check = "```bash\ncurl http://<server_ip>:<port>/v1/chat/completions -d '{\"model\":\"a2-model\"}'\n```"
        result = self.parse([item], verification=global_check)
        checks = result["scenarios"][0]["verify_cmds"]
        self.assertEqual(len(checks), 1)
        self.assertIn('"model":"a5-model"', checks[0])

    def test_local_readiness_does_not_replace_the_global_inference_check(self):
        item = scenario()
        item["steps"].append({"content": "```bash\ncurl http://<server_ip>:<port>/v1/models\n```"})
        result = self.parse([item], verification="```bash\ncurl http://<server_ip>:<port>/v1/embeddings\n```")
        self.assertEqual(len(result["scenarios"][0]["verify_cmds"]), 2)

    def test_qwen_a5_requests_target_served_name_and_keep_chat_benchmark(self):
        recipe = yaml.safe_load((ROOT / "models/en/Qwen/Qwen3.5-27B-Qwen3.6-27B.yaml").read_text(encoding="utf-8"))
        selected = [item for item in recipe["scenarios"] if item["npu"] == "Ascend 950DT"]
        result = self.parse(selected, verification=recipe["verification"])
        checks = result["scenarios"][0]["verify_cmds"]
        self.assertEqual(len(checks), 2)
        for command in checks:
            self.assertIn('"model":"qwen3.6"', command.replace(" ", ""))
            self.assertNotIn('"model":"qwen3.5"', command.replace(" ", ""))
        self.assertTrue(result["scenarios"][0]["benchmark_chat"])

    def test_glm_a5_uses_dtype_flags_instead_of_removed_sparse_c8_option(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                recipe = yaml.safe_load((ROOT / f"models/{language}/THUDM/GLM-5.yaml").read_text(encoding="utf-8"))
                selected = [dict(item, deployment="Single-Node") for item in recipe["scenarios"]
                            if item.get("strategy") == "single_node_A5"]
                result = self.parse(selected)
                self.assertEqual(len(result["scenarios"]), 1)
                args = shlex.split(result["scenarios"][0]["serve_cmd"].replace("\\\n", " "))
                config = json.loads(args[args.index("--additional-config") + 1])
                self.assertNotIn("enable_sparse_c8", config)
                self.assertEqual(args[args.index("--kv-cache-dtype") + 1], "fp8")
                self.assertEqual(args[args.index("--attention_config.indexer_kv_dtype") + 1], "fp8")

    def test_python_reranker_check_is_not_reduced_to_server_readiness(self):
        text = "```python\nimport requests\nrequests.post('http://<server_ip>:<port>/score', json={})\n```"
        result = self.parse([scenario()], verification=text, tasks=["reranking"])
        self.assertEqual(len(result["scenarios"][0]["verify_cmds"]), 1)


class RecipeWorkflowLoopTests(unittest.TestCase):
    def test_benchmark_refreshes_arrow_after_install_and_propagates_refresh_failure(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/_recipe_verify.yml").read_text(encoding="utf-8"))
        step = next(s for s in workflow["jobs"]["recipe-verify"]["steps"] if s.get("name") == "Install benchmark tools")
        # Replace only the external package installer; execute the real step's
        # shell, ordering and failure handling without downloading packages.
        installer = '''pip() {
  printf '%s\\n' "$*" >> "$PIP_CALLS"
  if [[ "$*" == *"pyarrow==25.0.0"* ]]; then
    return "$ARROW_INSTALL_EXIT"
  fi
  return 0
}
'''
        for install_exit in (0, 23):
            with self.subTest(install_exit=install_exit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                entry = root / "install-step.sh"
                entry.write_text(installer + step["run"], encoding="utf-8", newline="\n")
                calls_path = root / "pip-calls.txt"
                env = {**os.environ, "PIP_CALLS": str(calls_path), "ARROW_INSTALL_EXIT": str(install_exit)}
                shell = shlex.split(workflow["defaults"]["run"]["shell"])
                result = subprocess.run([part.replace("{0}", str(entry)) for part in shell],
                                        env=env, text=True, capture_output=True)
                calls = [shlex.split(line) for line in calls_path.read_text().splitlines()]
                aisbench = next(i for i, args in enumerate(calls) if "ais_bench_benchmark" in args)
                arrow = [i for i, args in enumerate(calls) if "pyarrow==25.0.0" in args]
                self.assertEqual(len(arrow), 1, result.stdout)
                self.assertGreater(arrow[0], aisbench)
                self.assertEqual(result.returncode, install_exit, result.stderr)

    def test_skip_and_failure_are_stamped_and_do_not_abort_later_recipes(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/_recipe_verify.yml").read_text(encoding="utf-8"))
        step = next(s for s in workflow["jobs"]["recipe-verify"]["steps"] if s.get("name") == "Verify recipes")
        # Exercise the real loop after CANN setup, with a verifier double that
        # returns the documented exit codes. Never start an NPU server here.
        loop = step["run"].replace(". /usr/local/Ascend/ascend-toolkit/set_env.sh", "true")
        for name, expected_rc, expected_status in [("skip", 0, "skip"), ("fail", 1, "fail")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir()
                (root / "scripts/verify-recipe.sh").write_text(
                    'case "$1" in *skip.yaml) exit 2 ;; *fail.yaml) exit 1 ;; *) exit 0 ;; esac\n',
                    encoding="utf-8", newline="\n",
                )
                (root / "recipe_list.txt").write_text(f"{name}.yaml\npass.yaml\n", encoding="utf-8", newline="\n")
                script = 'export PATH="/usr/bin:/bin:$PATH"\n' + loop.replace("/tmp/", "./")
                entry = root / "verify-step.sh"
                entry.write_text(script, encoding="utf-8", newline="\n")
                # Do not add shell flags absent from production: that hid the
                # tee pipeline swallowing the verifier's failure exit code.
                shell = shlex.split(workflow["defaults"]["run"]["shell"])
                result = subprocess.run([part.replace("{0}", str(entry)) for part in shell],
                                        cwd=root, text=True, capture_output=True)
                self.assertEqual(result.returncode, expected_rc, result.stderr)
                status = root / "verify-results" / f"{name}.status"
                self.assertTrue(status.is_file(), result.stdout)
                self.assertEqual(status.read_text().strip(), expected_status)
                self.assertEqual((root / "verify-results/pass.status").read_text().strip(), "pass")


class RecipeArtifactCompatibilityTests(unittest.TestCase):
    def test_single_flat_download_matches_its_selected_hardware_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "input"
            artifact.mkdir()
            recipe = "models/en/New/model.yaml"
            params = {"recipe_path": recipe, "hw_key": "atlas_800_a2",
                      "image": "test:a2", "scenarios": [{"npu": "A2"}]}
            (artifact / "model.params.json").write_text(json.dumps(params), encoding="utf-8")
            (artifact / "model.status").write_text("pass", encoding="utf-8")
            jobs = [{"job_id": family + "-test", "hardware_key": key,
                     "recipe_files": json.dumps([recipe]), "image": "test:" + family}
                    for family, key in [("a2", "atlas_800_a2"), ("a3", "atlas_800_a3")]]
            collect(artifact, root / "one", expected_plan={"include": jobs[:1]})
            self.assertEqual((root / "one/model.status").read_text().strip(), "pass")
            collect(artifact, root / "two", expected_plan={"include": jobs})
            self.assertEqual((root / "two/model.status").read_text().strip(), "unknown")
            runs = json.loads((root / "two/model.params.json").read_text())["hardware_runs"]
            self.assertEqual([(run["hw_key"], run["status"]) for run in runs],
                             [("atlas_800_a2", "pass"), ("atlas_800_a3", "unknown")])

    def test_missing_selected_hardware_cannot_be_hidden_by_a_sibling_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "input/recipe-verify-results-123-a2-test"
            artifact.mkdir(parents=True)
            recipe = "models/en/New/model.yaml"
            (artifact / "model.params.json").write_text(json.dumps({"recipe_path": recipe,
                    "hw_key": "atlas_800_a2", "scenarios": [{"npu": "A2"}]}), encoding="utf-8")
            (artifact / "model.status").write_text("pass", encoding="utf-8")
            plan = {"include": [{"job_id": family + "-test", "hardware_key": key,
                                  "recipe_files": json.dumps([recipe]), "image": "test:latest"}
                                 for family, key in [("a2", "atlas_800_a2"), ("a3", "atlas_800_a3")]]}
            collect(root / "input", root / "output", expected_plan={"include": plan["include"][:1]})
            self.assertEqual((root / "output/model.status").read_text().strip(), "pass")
            collect(root / "input", root / "output", expected_plan=plan)
            self.assertEqual((root / "output/model.status").read_text().strip(), "unknown")
            params = json.loads((root / "output/model.params.json").read_text())
            self.assertEqual(len(params["hardware_runs"]), 2)
            self.assertEqual(params["hardware_runs"][1]["hw_key"], "atlas_800_a3")

    def merge(self, statuses, missing_params_for=()):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for hardware, status in statuses.items():
            artifact = root / "input" / hardware
            artifact.mkdir(parents=True)
            params = {"recipe_path": "models/en/New/model.yaml", "hw_key": hardware,
                      "image": hardware + "-image", "scenarios": [] if status == "skip" else [{"npu": hardware}]}
            if hardware not in missing_params_for:
                (artifact / "model.params.json").write_text(json.dumps(params), encoding="utf-8")
            (artifact / "model.status").write_text(status, encoding="utf-8")
            (artifact / "model.recipe").write_text("models/en/New/model.yaml", encoding="utf-8")
        output = root / "output"
        self.assertEqual(collect(root / "input", output), ["model"])
        return json.loads((output / "model.params.json").read_text()), (output / "model.status").read_text()

    def test_all_pass_hardware_snapshots_are_kept_without_filename_overwrites(self):
        params, status = self.merge({"atlas_800_a2": "pass", "ascend_950dt": "pass"})
        self.assertEqual(status.strip(), "pass")
        self.assertEqual([s["npu"] for s in params["scenarios"]], ["ascend_950dt", "atlas_800_a2"])
        self.assertEqual(len(params["hardware_runs"]), 2)

    def test_a_failure_cannot_be_overwritten_by_another_hardwares_pass(self):
        _, status = self.merge({"atlas_800_a2": "pass", "ascend_950dt": "fail"})
        self.assertEqual(status.strip(), "fail")

    def test_skipped_hardware_does_not_claim_executed_scenarios(self):
        params, status = self.merge({"atlas_800_a2": "skip", "ascend_950dt": "pass"})
        self.assertEqual(status.strip(), "pass")
        self.assertEqual(params["scenarios"], [{"npu": "ascend_950dt"}])

    def test_all_skipped_is_not_published_as_pass(self):
        _, status = self.merge({"atlas_800_a2": "skip", "ascend_950dt": "skip"})
        self.assertEqual(status.strip(), "skip")

    def test_failure_before_params_capture_still_overrides_a_sibling_pass(self):
        _, status = self.merge({"atlas_800_a2": "pass", "atlas_800_a3": "fail"},
                               missing_params_for={"atlas_800_a3"})
        self.assertEqual(status.strip(), "fail")

    def test_success_without_a_params_snapshot_is_not_evidence_of_a_pass(self):
        _, status = self.merge({"atlas_800_a2": "pass"}, missing_params_for={"atlas_800_a2"})
        self.assertEqual(status.strip(), "unknown")


if __name__ == "__main__":
    unittest.main()
