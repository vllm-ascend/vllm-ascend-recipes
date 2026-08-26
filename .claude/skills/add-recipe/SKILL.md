---
name: add-recipe
description: Use when the user asks to add, contribute, create, update, or validate a vLLM-Ascend deployment recipe in this repo (e.g. "add a recipe for Qwen/Qwen3-XYZ", "create a recipe for {org}/{model}"). Walks through authoring the YAML at models/en/{Provider}/{Model}.yaml plus the zh 1:1 mirror, choosing upstream-aligned fields and this repo's extension fields (features / config_params / scenarios), validating with pnpm validate, and committing.
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
7. **Register CI verification when required.** Adding a recipe alone does not make it eligible for runtime CI or the site's verification badge. If the model/configuration is meant to be verified, add its exact recipe path, runner, mode, and four-part scenario selector to `.github/verification-targets.yaml`. Register every configuration intended to count toward the model's green/yellow/red verification summary. Do not add configurations that cannot yet be scheduled on an available runner.
8. **Register the runner weight alias when required.** Do not put weight paths or weight files in `.github/verification-targets.yaml`. For a single-node verification target, first arrange for the weights to be baked or mounted in the runner image, then add the recipe `model.model_id` to `models/_cache_paths.yaml` with its on-runner cache directory. The verification script resolves `your_model_path` from this mapping and skips safely if the image does not actually contain the weights.
9. **Validate.** Run `pnpm validate` (zod schema + interlock errors), then `pnpm check:recipes` (field-level / upstream-alignment rules, see "Validation" below), then `./scripts/format.sh` (validate + typecheck + lint + prettier, mirrors CI). Preview with `pnpm dev` at `/{provider}/{model}`.
10. **Commit.** Stage the recipe YAML(s), and include `.github/verification-targets.yaml` / `models/_cache_paths.yaml` only when they were intentionally updated for CI verification. Never stage `public/` or generated files. Message: `feat(recipe): add <Provider>/<Model>` (or `fix(recipe): ...` for updates).

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
| `hardware_overrides` / `strategy_overrides` | no | `{extra_args, extra_env}` per hardware / strategy; PD recipes additionally declare `strategy_overrides.pd_cluster.prefill/decode.nodes` (see below) |
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

Feature chips on the page pick their behavior from the step content automatically:
- referenced by a `{{key}}` placeholder or `%%CONFIG:key%%` marker → toggle chip (on/off both render, `opt_in_features` decides the default);
- flags already hardcoded in every step's command → always-on chip ("included in the baseline");
- never referenced and never hardcoded → default-OFF toggle; enabling appends the feature's `args` (or `env` exports) to the rendered `vllm serve` command.

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

### PD-cluster scenarios + declarative topology

Multi-node PD recipes (`tags: [pd-multinode]` / `strategy: pd_cluster`) must declare `strategy_overrides.pd_cluster` so the site can render the **node selector + Cluster env** panel and generate the per-node commands:

```yaml
compatible_strategies: [..., pd_cluster]
strategy_overrides:
  pd_cluster:
    prefill:
      nodes: { default: 1 }                  # prefill node count
    decode:
      nodes: { default: 1, atlas_800_a2: 4 } # per-hardware overrides win over default
```

- `nodes` = the number of nodes for that role. Derive it from the tutorial's `launch_online_dp.py` commands: `nodes = dp-size // dp-size-local`.
- Use per-hardware keys (`atlas_800_a2` / `atlas_800_a3`) when different hardware has different topology (e.g. DeepSeek-V4-Flash is 1P1D on A3 but 1P4D on A2's 8-machine PD).
- **Independent multi-P groups are NOT derivable from `dp-size // dp-local`**: a recipe with 2 independent prefill groups each `--dp-size 4 --dp-size-local 4` still declares `prefill.nodes: 2` explicitly. Don't try to infer the count from one launch command.
- The imperative `scenarios` below remain the CI source of truth; `strategy_overrides.pd_cluster` is the declarative site data. `parallelism` / `vllm_args` / `env` may also be added for upstream-compatible script generation, but the current Ascend site only consumes `nodes`.

**Launch command convention** — collapse the tutorial's per-node launch commands into exactly **2 commands** (first = prefill, second = decode):

```bash
# Prefill — each prefill node is its own DP master (dp-address = node IP)
python launch_online_dp.py --dp-size 4 --tp-size 4 --dp-size-local 4 --dp-rank-start 0 --dp-address xx.xx.xx.1 ...
# Decode — one DP group across nodes (dp-rank-start increments per node)
python launch_online_dp.py --dp-size 8 --tp-size 4 --dp-size-local 4 --dp-rank-start 0 --dp-address xx.xx.xx.3 ...
```

`node_entry.py` fills `--dp-address` (prefill → own IP, decode → master) and `--dp-rank-start` (decode → `group_offset × dp-size-local`) at runtime. Do NOT write four separate p0/p1/d0/d1 commands — the controller only reads `launch_blocks[0]` (prefill) and `[1]` (decode) and misassigns the rest.

**Placeholders the Cluster env panel substitutes** — keep these in the step code blocks, don't hardcode real values:

- Dotted node IPs `141.xx.xx.N` / `xx.xx.xx.N`: `N` is the 1-based pod index (prefill first, then decode). The site maps them to `$PREFILL_NODE_*` / `$DECODE_NODE_*`.
- `local_ip="141.xx.xx.N"` → the node's own IP.
- `nic_name="xxx"` → `$IFACE_NAME` (fabric NIC).
- `node0_ip="xxxx"` → prefill master IP (`$PREFILL_NODE_1`).
- `<prefill_ip>` / `<decode_ip>` → prefill/decode master IP (Qwen3-235B style).

> `use_ascend_direct` inside the prefill `kv_connector_extra_config` appears only on prefill node 0 in some tutorials (e.g. Kimi-K2.6) — treat it as a tutorial inconsistency; put it on all prefill nodes or drop it consistently (DeepSeek-V4-Flash has none).

## Hard rules

- Boolean toggles live ONLY in `features`; `config_params` is values only.
- Default state derives from `opt_in_features` (absent = on) — don't add a `default` field to features.
- `scenario.strategy` ⊆ `compatible_strategies`; tags must match the pipeline routing (a2-single / a3-single / pd-multinode).
- A configuration counts toward the site's verification status only after it is explicitly registered in `.github/verification-targets.yaml`; recipe `meta.hardware` and `scenarios` alone do not create a CI target.
- Keep verification metadata separate from weights: `.github/verification-targets.yaml` selects the target and runner, while `models/_cache_paths.yaml` maps a model ID to an already available runner-cache directory.
- Ascend install is docker-only: `install.pip: false`; `guide: ""`.
- `en/` and `zh/` field structures must be identical.
- The official vllm-ascend tutorial (`docs/source/tutorials/models/<Model>.md`) is the source of truth for scenario content and serve parameters; mirror it, don't improvise.
- Keep the scenario serve commands the CI-execution source of truth; when editing flags, update base_args/base_env/features too.
- Resolve `{{ vllm_ascend_version }}` literals in env_setup to the pinned version — the site only substitutes `|vllm_ascend_version|`, so curly-brace placeholders render verbatim to users.

### Content conventions (learned from page QA)

- **en files use English display values, zh files use Chinese**: `deployment` / `case` in `models/en/**` must be English (e.g. `Single-Node`, `Multi-Node PD Separation`); the zh mirror keeps Chinese.
- **No backslash escapes in prose**: write `1~2 cards` (plain tilde), never `1\~2` — the page renders `\~` literally.
- **Install order**: `env_setup.container` is the recommended path; the page defaults the tab to container when both exist. Keep `container` before `pip` in the YAML.
- **Performance section carries BOTH accuracy and benchmark**: the "More Info" tab renders `performance.accuracy` + `performance.benchmark` — don't ship only one. Always link AISBench/vllm-benchmark, never mention them bare.
- **Use official hardware names**: write `Atlas 300I DUO` (or `Atlas 800I A2/A3`), not generic "Atlas inference products".
- **Weight download is required**: every recipe must have `weight_download` (the page renders weight chips near the top from it).
- **Blockquotes**: lines starting with `>` are rendered as styled blockquotes by the site — don't use literal `>` inside prose expecting it to stay text.
- **Never nest code blocks inside blockquotes**: lines like `> ```bash` make the renderer emit code with a literal `>` prefix. Keep the `>` note as plain text and put the command in a standalone fenced block below it.
- **No English section names in zh prose**: in `models/zh/**`, refer to sections by their Chinese page titles (e.g. "参见下方“功能验证”部分", never "参见下方 `verification` 部分"). The YAML field keys stay as-is (`verification:`); only prose changes.
- **No mixed-language product names in zh**: write `Atlas 300I DUO`, not "Atlas 300I DUO昇腾产品" (or similar suffixes). Keep zh product names identical to en (brand names are not translated).
- **Image version is model-linked, not global**: resolve `{{ vllm_ascend_version }}` to the version the tutorial specifies for that model (e.g. Qwen3.5-27B/Qwen3.6-27B → `v0.18.0rc1`, with Atlas 300I DUO → `v0.23.0rc1-310p` per tutorial), not one global version. Keep the tutorial's own "validated against" / "supported starting" statements untouched.
- **`meta.tasks` must not duplicate `model.modality`**: the page renders both as tags, so `tasks: [text]` + `modality: text` shows "text" twice. Only add tasks values that differ from the modality.
- **Weight download placement**: the page renders `weight_download` chips automatically inside the Prerequisites tab — don't duplicate weight links in `overview` / `prerequisites` prose.
- **`overview` is a concise model description only**: 1–2 short paragraphs about the model itself (architecture / parameter count / family / purpose). Do NOT put supported hardware, software features, deployment topology, or version-validation notes in `overview` — those belong in `meta.hardware`, `features`, `scenarios`, and `model.min_vllm_version` respectively. Drop the "This document will show…" boilerplate.
- **No hardware badges at the top of the page**: `meta.hardware` is CI/status metadata, not a header badge. Hardware availability is communicated through the scenario selector (`scenarios[].npu`) and the preparation/deployment sections — do not duplicate it in the page header or `overview`.

### Atlas 300I DUO (310p)

When the tutorial covers Atlas 300I DUO (inference products), mirror it faithfully:

- `meta.hardware` gains `atlas_300i_duo: verified`; `model.docker_image` gains `atlas_300i_duo: "<image>-310p"`.
- `env_setup.container` gets a `300I DUO` entry using the `-310p` image (mount only the inference devices: davinci0 + davinci_manager/devmm_svm/hisi_hdc).
- Add a scenario with `npu: Atlas 300I DUO`, `tags: [310p-single]`, `strategy: single_node_310p` (add `single_node_310p` to `compatible_strategies`); 300I is TP-only (`--dtype float16`, conservative `--max-model-len`, per-tutorial values stay literal).
- `scripts/verify-recipe.sh` skips `300I` scenarios on the A2/A3 runners — keep that skip in place.

### Referenced helper scripts

When a tutorial's PD/multi-node steps reference helper scripts (`launch_online_dp.py`, `run_dp_template.sh`, `load_balance_proxy_server_example.py`), either embed them in the step (like DeepSeek-V2-Lite) **or** link to the upstream examples (`https://github.com/vllm-project/vllm-ascend/blob/main/examples/...`) — never leave a dangling filename with no source.

## Validation (pnpm validate)

The zod schema (`src/lib/schema.ts`) enforces, besides field types, these interlock rules:

1. `opt_in_features` references declared `features`;
2. `variants` present → `default` variant exists;
3. `scenario.strategy` is listed in `compatible_strategies`;
4. step `{{name}}` placeholders resolve to `config_params` or `features`;
5. `%%CONFIG:key%%` markers resolve to an `extra_config` key or feature.

### Field-level validation (pnpm check:recipes)

`scripts/validate-recipe-fields.ts` runs on every PR, push to main, and the
nightly prepare stage (before any cluster is touched). It checks what the zod
schema cannot express:

1. **Required sections**: `meta` (title/slug/provider/description/date_added),
   `model`, `overview`, `weight_download` (non-empty sources), `env_setup`,
   `scenarios`, `references`.
2. **Upstream-aligned model fields**: `model_id` must be `<org>/<name>`;
   `architecture` ∈ dense|moe; `variants` → `default` exists and every variant
   has `precision`; every `feature` has `args` or `env`; `opt_in_features ⊆
   features`; `config_params.type` ∈ number|string|bool.
3. **Strategy & Ascend conventions**: `scenario.strategy` must be listed in
   `compatible_strategies`; `guide == ""`; `model.install.pip === false`
   (docker-only).
4. **Scenario / step fields**: each scenario has npu/precision/deployment/case;
   non-empty steps with title+content; `{{name}}` placeholders resolve to the
   top-level **or scenario-level** `config_params` / `features`;
   `%%CONFIG:key%%` resolves to `extra_config` / `features`; script-backed
   scenarios require `test_id` / `npu_per_node` / `service-check` and every
   `{{script:x}}` reference must exist.
5. **en/zh parity**: the zh mirror must have the same field-path structure as
   the en file.

Run it locally after editing a recipe — a real example it caught:
`Qwen3-30B-A3B` used `strategy: multi_node_dp` without listing it in
`compatible_strategies`.

## Commit checklist

- `pnpm validate` + `pnpm check:recipes` + `./scripts/format.sh` green;
- `zh/` mirror committed with the `en/` change;
- If runtime verification is expected, the intended scenario selectors are registered in `.github/verification-targets.yaml`; any required runner cache alias is registered in `models/_cache_paths.yaml` only after the weights are available in that runner image;
- No `public/` or generated files staged.
