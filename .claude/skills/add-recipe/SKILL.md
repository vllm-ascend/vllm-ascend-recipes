---
name: add-recipe
description: Use when the user asks to add, contribute, create, update, or validate a vLLM-Ascend deployment recipe in this repo (e.g. "add a recipe for Qwen/Qwen3-XYZ", "create a recipe for <org>/<model>"). Walks through authoring the YAML at models/en/<Provider>/<Model>.yaml plus the zh 1:1 mirror, choosing upstream-aligned fields and this repo's extension fields (features / config_params / scenarios), validating with pnpm validate, and committing.
---

# Add a new vLLM-Ascend recipe

Recipes are YAML files at `models/en/<Provider>/<Model>.yaml` (English, source of truth) with a `models/zh/<Provider>/<Model>.yaml` mirror (identical field structure, Chinese descriptions). The site is a static Astro app generated from these files; `src/lib/schema.ts` (zod) is the validation authority.

## End-to-end steps

1. **Confirm the model id.** Get the exact `<org>/<repo>` string (strip `https://modelscope.cn/models/` / `https://huggingface.co/` prefixes). The page URL is derived from the YAML path (`models/en/<Provider>/<Model>.yaml` → `/<provider>/<model>`), and `model.model_id` should be the exact org/repo.
2. **Fetch model metadata.** Pull `config.json` from HF/ModelScope: `architecture` (`moe` if `num_experts` / `*MoE*` arch names, else `dense`), `parameter_count` / `active_parameters`, `context_length` (`max_position_embeddings`, or `text_config.max_position_embeddings` for VL models).
3. **Read the model README — don't skip.** Mine it for: `min_vllm_version` / `nightly_required`, `dependencies`, parser flags (`--tool-call-parser`, `--reasoning-parser`, `--enable-auto-tool-choice`), MTP/quantized companion repos (→ `spec_decoding` feature or `variants` with `model_id` override), recommended serve flags, and hardware guidance.
4. **Cross-check vllm-ascend support.** Verify the model works on the vllm-ascend version you claim; required flags go into `model.base_args`, base env into `model.base_env`. Copy any `--speculative-config` JSON verbatim from the README.
   **Reference the official tutorial** — pull `docs/source/tutorials/models/<Model>.md` from the matching vllm-ascend tag (e.g. `v0.23.0rc1`) and use it as the single source of truth for the recipe: scenario division (single-node / multi-node DP / PD separation / request forwarding), serve flags, env vars and per-scenario parameters must match the tutorial. Do not invent scenarios or parameters the tutorial doesn't cover.
5. **Author `models/en/...`.** Follow the schema below. Use an existing recipe (e.g. `models/en/Qwen/Qwen3-30B-A3B.yaml` or `models/en/DeepSeek/DeepSeek-V4-Flash.yaml`) as a template. Keep the tutorial content in our fields (`overview` / `prerequisites` / `env_setup` / `scenarios`); leave `guide` empty.
6. **Mirror to `models/zh/...` 1:1.** Same field structure (meta/model/features/variants/strategies/overrides/dependencies/config_params/scenarios/extra_config), only descriptions in Chinese. If the zh file is missing, the site falls back to English.
7. **Validate.** Run `pnpm validate` (fails fast on schema + interlock errors), then `./scripts/format.sh` (validate + typecheck + lint + prettier, mirrors CI). Preview with `pnpm dev` at `/{provider}/{model}`.
8. **Commit.** Stage only the recipe YAML(s) — never `public/` or generated files. Message: `feat(recipe): add <Provider>/<Model>` (or `fix(recipe): ...` for updates).

## YAML schema (top-level, 总-分)

### Top-level fields

| Field | Required | Notes |
|---|---|---|
| `meta` | yes | Display metadata (see below) |
| `model` | yes | Model info + upstream fields (see below) |
| `overview` / `weight_download` / `env_setup` / `scenarios` / `references` | yes | Our tutorial fields; `scenarios` is the CI-execution baseline |
| `prerequisites` / `quantization` / `verification` / `performance` / `evaluation` / `tuning` / `faq` | no | Optional page sections |
| `features` | no | Upstream feature declarations (`label` / `description` / `args` / `env`); boolean toggles live here |
| `opt_in_features` | no | Feature keys that default OFF (must reference `features`) |
| `variants` | no | Upstream variants; `default` variant required |
| `compatible_strategies` | no | Deployment strategies, interlocked with `scenarios[].strategy` |
| `hardware_overrides` / `strategy_overrides` | no | `{extra_args, extra_env}` per hardware / strategy |
| `dependencies` | no | Extra installs: `note` + `command` |
| `guide` | no | Upstream tutorial body — keep `""` (content lives in our fields) |
| `extra_config` | no | Toggleable additional-config chips (see scenarios) |
| `config_params` | no | Editable value params substituted via `{{name}}` |

### `meta`

`title`, `slug`, `provider`, `description`, `date_added` (never change) required; optional `date_updated`, `difficulty` (`beginner|intermediate|advanced`), `tasks`, `performance_headline`, `related_recipes`, `hardware` (`atlas_800_a2/a3` → `verified|experimental|unsupported`).

### `model`

`model_id`, `architecture` (`dense|moe`), `parameter_count`, `active_parameters` (MoE; `null` dense), `context_length`, `modality` required. Optional upstream fields: `base_args` (flags every scenario needs), `base_env` (env every scenario needs), `docker_image` (string or per-hardware object `atlas_800_a2/a3`), `nightly_required` (true when min version isn't stable), `install` (tabs `pip`/`docker`: `false` hides, object overrides `command`/`note`).

### `features` / `opt_in_features`

```yaml
features:
  spec_decoding:            # upstream key — never use `mtp`
    label: MTP Spec Decoding
    description: MTP speculative decoding
    args: ["--speculative-config", '{"num_speculative_tokens": 1, "method": "mtp", "enforce_eager": true}']
  flashcomm1:
    label: FlashComm1
    env: { VLLM_ASCEND_ENABLE_FLASHCOMM1: "1" }
  prefix_caching:
    label: Prefix Caching
    args: ["--enable-prefix-caching"]
    flag_when_false: "--no-enable-prefix-caching"   # page extension
opt_in_features: [prefix_caching]   # default OFF; features absent here default ON
```

Every feature needs `args` or `env`. `flag_when_false` (page extension) is the text rendered when the toggle is OFF.

### `variants` / `compatible_strategies`

```yaml
variants:
  default:                     # required whenever variants is present
    precision: W8A8            # upstream enum bf16|fp8|nvfp4|fp4|int4|int8|awq|gptq|mxfp4; W8A8 = Ascend extension
    vram_minimum_gb: 341       # ceil(params × bytes_per_param × 1.2)
    description: ...
  w8a8_mtp:                    # quantized variant in another repo
    model_id: Eco-Tech/DeepSeek-V4-Flash-w8a8-mtp
    precision: W8A8
    vram_minimum_gb: 341
compatible_strategies: [single_node_A2, single_node_A3, pd_cluster]
```

### `config_params` and placeholders

`config_params` holds only editable VALUE params (`default` / `type` / `description`). Steps reference them with `{{name}}`; boolean toggles come from `features` via the same `{{name}}` syntax (on → `args`/`env`, off → `flag_when_false`). Rendered flags are highlighted in the chip color (`%%HL%%` markers are injected automatically; copy buttons get clean text).

> Only parameters with a **single tutorial baseline** become `config_params`. Scenario-specific values (e.g. different `--max-model-len` per precision / hardware, like GLM-5's 200000/40960/32768/131072) stay literal in the step command — a top-level default cannot represent them all.

### `scenarios` and `extra_config`

```yaml
scenarios:
  - npu: Atlas 800I A3
    precision: W8A8
    deployment: 单节点-多卡
    case: 高吞吐
    tags: [a3-single]          # a2-single | a3-single | pd-multinode → CI routing
    strategy: single_node_A3   # must be listed in compatible_strategies
    steps:
      - title: Start the server
        content: |-
          ```bash
          vllm serve ... --max-model-len {{max_model_len}} \
              {{spec_decoding}} \
              %%CONFIG:dsa-cp%%--additional-config '{"enable_dsa_cp": true}' %%/CONFIG:dsa-cp%%
          ```
extra_config:
  - key: dsa-cp
    label: DSA CP
```

`%%CONFIG:key%%...%%/CONFIG:key%%` keeps the wrapped text when the chip is on, removes it when off; optional per-step `config_values` (`enabled`/`disabled`) replaces instead.

## Hard rules

- Boolean toggles live ONLY in `features`; `config_params` is values only.
- Default state derives from `opt_in_features` (absent = on) — don't add a `default` field to features.
- `scenario.strategy` ⊆ `compatible_strategies`; tags must match the pipeline routing (a2-single / a3-single / pd-multinode).
- Ascend install is docker-only: `install.pip: false`; `guide: ""`.
- `en/` and `zh/` field structures must be identical.
- The official vllm-ascend tutorial (`docs/source/tutorials/models/<Model>.md`) is the source of truth for scenario content and serve parameters; mirror it, don't improvise.
- Keep the scenario serve commands the CI-execution source of truth; when editing flags, update base_args/base_env/features too.

## Validation (pnpm validate)

The zod schema (`src/lib/schema.ts`) enforces, besides field types, these interlock rules:

1. `opt_in_features` references declared `features`;
2. `variants` present → `default` variant exists;
3. `scenario.strategy` is listed in `compatible_strategies`;
4. step `{{name}}` placeholders resolve to `config_params` or `features`;
5. `%%CONFIG:key%%` markers resolve to an `extra_config` key or feature.

## Commit checklist

- `pnpm validate` + `./scripts/format.sh` green;
- `zh/` mirror committed with the `en/` change;
- No `public/` or generated files staged.
