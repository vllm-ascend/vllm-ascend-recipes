# vLLM-Ascend Recipes

Community-maintained deployment recipes for running LLMs on Ascend NPUs with [vllm-ascend](https://github.com/vllm-project/vllm-ascend).

Pick a model, choose your hardware, copy the `vllm serve` command and run. Covers Atlas 800 A2 / A3 and other Ascend NPU hardware.

🌐 **Live site**: <https://vllm-ascend.github.io/vllm-ascend-recipes/>

## Features

- **Cascade deployment guides** — Select NPU type, precision, and deployment mode to view the matching `vllm serve` command with all flags pre-filled.
- **i18n** — English and Chinese content, switchable from the header.
- **Searchable browse page** — Filter by NPU, architecture, modality, or free-text search across title / HF id / provider / description.
- **Hardware support badges** — Each recipe declares which NPU SKUs have been verified.
- **JSON API** — Programmatic access to the model list at `/models.json`.
- **PR previews** — Every PR gets a Netlify preview link posted as a comment.
- **Light / dark mode** — Toggled from the header; preference is persisted in `localStorage` under the `theme` key.

## Quick start

```bash
pnpm install
pnpm dev
```

Open <http://localhost:4321/vllm-ascend-recipes/>.

> The dev server runs in the foreground by default. To run it detached, use `astro dev --background` (then `astro dev status` / `astro dev stop` / `astro dev logs` to manage it — see [AGENTS.md](AGENTS.md)).

## Scripts

| Command | What it does |
|---------|--------------|
| `pnpm dev` | Start the Astro dev server (foreground) |
| `pnpm build` | Produce a static build in `dist/` |
| `pnpm preview` | Serve the production build locally |
| `pnpm validate` | Validate every YAML in `models/` against the zod schema |
| `pnpm typecheck` | Run `astro check` (Astro + TypeScript) |
| `pnpm lint` | Run ESLint |
| `pnpm lint:fix` | Run ESLint with `--fix` |
| `pnpm format` | Run Prettier `--write` |
| `pnpm format:check` | Run Prettier `--check` |
| `./scripts/format.sh` | Run validate + typecheck + lint + format:check (mirrors CI) |
| `./scripts/format.sh --fix` | Run prettier `--write` + eslint `--fix`, then re-check |

`format.sh` is the same gate CI runs — use it locally before pushing to catch issues early.

## Project layout

```
.
├── astro.config.mjs          # Astro + React + Tailwind setup, base path = /vllm-ascend-recipes
├── eslint.config.mjs         # ESLint flat config
├── public/                   # Static assets (favicon)
├── scripts/
│   ├── format.sh             # CI-mirroring check script
│   └── validate-yaml.ts      # YAML schema validation
├── models/
│   ├── en/                   # English content (source of truth)
│   │   ├── DeepSeek/
│   │   ├── Qwen/
│   │   └── THUDM/
│   └── zh/                   # Chinese content (mirrors `en/`)
│       ├── DeepSeek/
│       ├── Qwen/
│       └── THUDM/
└── src/
    ├── components/           # Astro (.astro) + React (.tsx) components
    │   ├── CascadeSelector.tsx     # NPU → precision → deployment → case picker
    │   ├── CodeBlock.astro         # Shared code-block wrapper
    │   ├── EnvSetupTabs.tsx        # pip / container env-setup tabs
    │   ├── LanguageToggle.tsx      # EN / 中文 switch (header)
    │   ├── MarkdownContent.astro   # Renders YAML markdown bodies + Sphinx notes
    │   ├── ModelCard.astro         # Card on the home + browse page
    │   ├── PrepareTabs.tsx         # Overview / prerequisites / quantization tabs
    │   ├── ReferenceTabs.tsx       # Performance / tuning / FAQ / references tabs
    │   ├── SearchBar.tsx           # Client-side filtering on /browse
    │   ├── Tag.astro               # Small labeled chip
    │   ├── ThemeToggle.tsx         # Light / dark switch (header)
    │   └── WeightDownloadTabs.tsx  # Per-weight-version source tabs
    ├── layouts/
    │   └── BaseLayout.astro        # Page chrome + inline theme/lang boot script
    ├── lib/
    │   ├── i18n.ts          # en/zh translation dictionary + types
    │   ├── models.ts        # YAML loading + model list helpers
    │   ├── schema.ts        # zod schema (single source of truth for YAML shape)
    │   ├── types.ts         # TypeScript types derived from the schema
    │   └── useLang.ts       # React hook: current language + t() helper
    ├── pages/
    │   ├── index.astro            # Home
    │   ├── browse.astro           # Searchable list of all models
    │   ├── models.json.ts         # JSON endpoint: full model list for tooling
    │   └── [provider]/[model].astro  # Recipe page (one per model)
    └── styles/
        └── global.css       # Tailwind v4 entry, design tokens, prose styles
```

## Adding a new model

1. **Create the file** at `models/en/{Provider}/{Model-Name}.yaml`. The URL is built from the directory + filename (e.g. `models/en/Qwen/Qwen3-30B-A3B.yaml` → `/qwen/qwen3-30b-a3b`).
2. **Fill in the YAML** following the [schema](#yaml-schema) below. Use an existing recipe (e.g. `models/en/Qwen/Qwen3-30B-A3B.yaml`) as a template.
3. **Validate**: `pnpm validate` — fails fast on schema errors.
4. **Preview**: `pnpm dev` — open the page at `/{provider}/{model}`.
5. **Translate** to `models/zh/{Provider}/{Model-Name}.yaml` with the same structure. If a model is missing in `zh/`, the site falls back to the English content.

Each model page is a single static file generated by Astro at build time — no runtime database or server.

## YAML schema

The schema lives in [`src/lib/schema.ts`](src/lib/schema.ts) and is enforced by [`scripts/validate-yaml.ts`](scripts/validate-yaml.ts). What follows is the practical reference; the zod schema is the authority.

### Top-level fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `meta` | yes | object | Display metadata (see below) |
| `model` | yes | object | Model info (HuggingFace id, arch, params, etc.) |
| `overview` | yes | string | Markdown intro shown above the preparation section |
| `weight_download` | yes | array | Download sources grouped by weight version |
| `env_setup` | yes | object | pip and/or container setup instructions (at least one) |
| `scenarios` | yes | array | Deployment scenarios (cascade: NPU → precision → deployment → case) |
| `references` | yes | array | Reference links shown at the bottom of the page |
| `quantization` | no | object | Quantization guide (rendered under the Preparation tabs) |
| `prerequisites` | no | array | Checklist items shown before env setup |
| `extra_config` | no | array | Toggleable Ascend-specific config keys (used by `CascadeSelector`) |
| `performance` | no | object | Accuracy + benchmark markdown blocks |
| `evaluation` | no | object | Legacy accuracy/performance blocks (DeepSeek, Qwen recipes) |
| `verification` | no | string | Service verification (curl examples) |
| `tuning` | no | string | Tuning / best-practices markdown |
| `faq` | no | string | FAQ markdown |

### `meta`

| Field | Required | Description |
|-------|----------|-------------|
| `title` | yes | Display name (e.g. `Qwen3-235B-A22B`) |
| `slug` | yes | URL slug, lowercase, hyphenated (e.g. `qwen3-235b-a22b`). Currently informational — the live URL is derived from the YAML path, not this field. |
| `provider` | yes | Provider display name (e.g. `Qwen`, `THUDM`, `DeepSeek`) |
| `description` | yes | One-paragraph summary, rendered with markdown |
| `date_added` | yes | ISO date (`YYYY-MM-DD`) — drives the sort order on home + browse |
| `tasks` | no | Task types, e.g. `[text]`, `[text, image]` |
| `performance_headline` | no | One-line performance summary shown on the card |
| `hardware` | no | Per-NPU status: `verified` / `experimental` / `unsupported` (see [Hardware keys](#hardware-keys)) |

### `model`

| Field | Required | Description |
|-------|----------|-------------|
| `model_id` | yes | HuggingFace / ModelScope model id (e.g. `Qwen/Qwen3-235B-A22B`) |
| `min_vllm_version` | no | Minimum vLLM-Ascend version required |
| `architecture` | yes | `dense` or `moe` |
| `parameter_count` | yes | Total parameters (e.g. `235B`, `30.5B`) |
| `active_parameters` | MoE only | Activated params per token (e.g. `22B`); `null` for dense |
| `context_length` | yes | Native context length in tokens |
| `modality` | yes | `text`, `vision`, `audio`, `image`, etc. |

### `weight_download`

Array of weight versions. Each entry has a `weight_version` label and a `sources` array. Each source has `source` (display name), `url`, and `command` (markdown with a code block):

```yaml
weight_download:
  - weight_version: BF16
    sources:
      - source: ModelScope
        url: https://www.modelscope.cn/models/Qwen/Qwen3-30B-A3B
        command: |-
          ```bash
          modelscope download --model Qwen/Qwen3-30B-A3B
          ```
      - source: HuggingFace
        url: https://huggingface.co/Qwen/Qwen3-30B-A3B
        command: |-
          ```bash
          huggingface-cli download Qwen/Qwen3-30B-A3B
          ```
  - weight_version: W8A8 (Pre-quantized)
    sources:
      - source: ModelScope
        url: https://www.modelscope.cn/models/Eco-Tech/Qwen3-30B-A3B-w8a8
        command: ...
```

### `env_setup`

At least one of `pip` or `container` is required. `container` is keyed by NPU type (e.g. `A3`, `A2`):

```yaml
env_setup:
  pip:
    content: |-
      ```bash
      pip install vllm-ascend==...
      ```
  container:
    A3:
      content: |-
        ```bash
        docker pull quay.io/ascend/vllm-ascend:...
        ```
    A2:
      content: |-
        ...
```

### `scenarios`

The heart of the cascade selector. Each scenario is a 4-tuple `(npu, precision, deployment, case)` plus a list of `steps` that contain the `vllm serve` command. The four fields are free-form strings — they label the chip in each selector and don't have to be English:

```yaml
scenarios:
  - npu: Atlas 800I A3
    precision: W8A8
    deployment: 单节点-TP
    case: 高吞吐
    default_configs:                              # optional: pre-select extra_config keys
      - mtp-spec-decoding
      - async-scheduling
    steps:
      - title: Start the server
        content: |-
          ```bash
          vllm serve Qwen/Qwen3-30B-A3B \
              --quantization ascend \
              --tensor-parallel-size 8
          ```
      - title: Verify the service
        content: |-
          ```bash
          curl http://localhost:8000/v1/chat/completions ...
          ```
```

`extra_config` defines the toggleable chips on the right of the selector. Each toggle wraps any text inside a scenario step with `%%CONFIG:key%%...%%/CONFIG:key%%` markers; the `CascadeSelector` removes the block when the chip is off and keeps it (with a colored highlight) when on:

```yaml
extra_config:
  - key: mtp-spec-decoding
    label: MTP Speculative Decoding
  - key: prefix-caching
    label: Prefix Caching
  - key: async-scheduling
    label: Async Scheduling

scenarios:
  - npu: Atlas 800I A3
    precision: W8A8
    deployment: 单节点-TP
    case: 高吞吐
    steps:
      - title: Start the server
        content: |-
          ```bash
          vllm serve Qwen/Qwen3-30B-A3B \
              --quantization ascend \
              --tensor-parallel-size 8 \
              %%CONFIG:mtp-spec-decoding%%--speculative-config '{"method":"mtp"}' %%/CONFIG:mtp-spec-decoding%%\
              %%CONFIG:prefix-caching%%--enable-prefix-caching %%/CONFIG:prefix-caching%%\
              %%CONFIG:async-scheduling%%--async-scheduling %%/CONFIG:async-scheduling%%
          ```
```

A step can also override what gets inserted when the chip is on/off, instead of keeping/removing the wrapped text. Provide `config_values` with `enabled` / `disabled` strings:

```yaml
steps:
  - title: Start the server
    config_values:                              # optional per-step override
      prefix-caching:
        enabled: "--enable-prefix-caching"
        disabled: "--no-enable-prefix-caching"
    content: |-
      ```bash
      vllm serve Qwen/Qwen3-30B-A3B \
          %%CONFIG:prefix-caching%% %%/CONFIG:prefix-caching%%
      ```
```

When `config_values` is set, the inner text is **replaced** with `enabled` (chip on) or `disabled` (chip off). When it isn't, the inner text is either **kept** (chip on) or **removed** (chip off).

### `references`

```yaml
references:
  - title: vLLM-Ascend docs
    url: https://docs.vllm.ai/projects/ascend/
  - title: Model card on HuggingFace
    url: https://huggingface.co/Qwen/Qwen3-30B-A3B
```

### Hardware keys

The `meta.hardware` object uses these keys (defined in the schema):

- Atlas: `atlas_800_a3`, `atlas_800_a2`
- AMD: `mi300x`, `mi325x`, `mi355x`
- NVIDIA: `h200`, `b200`, `gb200`

Values are `verified` (recipe was validated end-to-end), `experimental` (works but not fully validated), or `unsupported` (known not to work).

## Markdown extensions

`MarkdownContent.astro` understands a few Sphinx-style syntaxes on top of plain markdown — handy for reusing existing vllm-ascend docs:

| Syntax | Renders as |
|--------|------------|
| `:::{note}\n... :::` | Styled blockquote with "Note" label |
| `:::{warning}\n... :::` | Styled blockquote with "Warning" label |
| `:::{tip}\n... :::` | Styled blockquote with "Tip" label |
| `:::{note} Title\nbody :::` | Same, with a custom title |
| `\|vllm_ascend_version\|` | Substituted with the current `vllm-ascend` version (e.g. `v0.22.1rc1`) |
| Pipe tables (`\| ... \|`) | HTML table with sticky header |

## i18n

- The default language is **English**. Developers maintain `models/en/` as the source of truth.
- The Chinese translation lives in `models/zh/`. If a recipe is missing there, the site silently falls back to the English content (URL stays the same; only the language toggle is affected).
- UI strings live in [`src/lib/i18n.ts`](src/lib/i18n.ts) as a small `translations` dictionary. The inline script at the bottom of `BaseLayout.astro` reads the saved `lang` from `localStorage` and applies it to every element carrying a `data-i18n` attribute.
- Markdown bodies are translated wholesale — the page renders the matching `models/{lang}/` file.

## Deployment

Pushes to `main` trigger GitHub Actions:

- [`.github/workflows/lint.yml`](.github/workflows/lint.yml) — runs validate, typecheck, lint, format:check.
- [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) — builds the static site and deploys to GitHub Pages.
- [`.github/workflows/preview-build.yml`](.github/workflows/preview-build.yml) + [`preview-deploy.yml`](.github/workflows/preview-deploy.yml) — every PR gets a Netlify preview URL posted as a comment (artifacts expire in 3 days).

Configure Pages in repo Settings → Pages → Source: **GitHub Actions**.

## Contributing

1. Fork the repo, create a branch.
2. Add or modify a recipe under `models/en/` (and the `zh/` mirror).
3. Run `./scripts/format.sh` — all four steps must pass.
4. Open a PR. The Netlify preview link will appear as a comment once CI is done.

If you only want to add a model without the full recipe (e.g. upstream support is experimental), keep `weight_download` and `scenarios` minimal and mark `meta.hardware` as `experimental`.
