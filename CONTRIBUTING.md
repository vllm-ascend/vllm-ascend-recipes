# Contributing to vLLM-Ascend Recipes

Welcome, and thanks for your interest in contributing to **vLLM-Ascend Recipes**!

This repo hosts community-maintained deployment recipes for running LLMs on Ascend NPUs
with [vLLM-Ascend](https://github.com/vllm-project/vllm-ascend). Each recipe answers the
question: **How do I run model X on Ascend NPU Y with precision Z?** The site itself is a
static Astro app that renders structured YAML recipes — there is no runtime database.

The live site is at <https://vllm-ascend.github.io/vllm-ascend-recipes/>.

> ℹ️ **Scope.** This repo only holds **recipes and the site that renders them**. Bugs in the
> vLLM-Ascend **engine / runtime** belong in [vllm-project/vllm-ascend](https://github.com/vllm-project/vllm-ascend/issues).

---

## Table of contents

- [Ways to contribute](#ways-to-contribute)
- [Code of conduct](#code-of-conduct)
- [Before you start](#before-you-start)
- [Development setup](#development-setup)
- [Adding or updating a recipe](#adding-or-updating-a-recipe)
- [Translating recipes (i18n)](#translating-recipes-i18n)
- [Contributing to the site / frontend](#contributing-to-the-site--frontend)
- [Coding standards](#coding-standards)
- [Commit messages and DCO sign-off](#commit-messages-and-dco-sign-off)
- [Pull request workflow](#pull-request-workflow)
- [RFC process](#rfc-process)
- [Reporting bugs and requesting recipes](#reporting-bugs-and-requesting-recipes)
- [License](#license)

---

## Ways to contribute

You don't need to write code to help. Any of these are welcome:

- **Add a recipe** for a model + NPU + precision combination that isn't covered yet.
- **Improve an existing recipe** — fix a wrong flag, add a faster config, update performance numbers, mark a hardware SKU as `verified`.
- **Translate** an English recipe into Chinese (`models/zh/` mirror).
- **Fix the site** — UI bugs, accessibility, search, new components.
- **Improve tooling / CI** — validation, build, preview pipelines.
- **Triage issues**, review PRs, and answer questions in Discussions.

## Code of conduct

Be kind and professional. We follow the same spirit as the upstream
[vLLM-Ascend community](https://github.com/vllm-project/vllm-ascend). Harassment of any
kind is not tolerated. If you experience or witness unacceptable behavior, contact the
maintainers privately by opening a private security advisory or emailing a maintainer
listed in the CODEOWNERS file.

## Before you start

1. **Search first.** Look through [open issues](https://github.com/vllm-ascend/vllm-ascend-recipes/issues?q=is%3Aissue+sort%3Acreated-desc+)
   and [Discussions](https://github.com/vllm-ascend/vllm-ascend-recipes/discussions) to
   avoid duplicating work.
2. **Open an issue first** for non-trivial changes (new recipe category, schema change,
   UI redesign) so we can align before you spend time coding. See [RFC process](#rfc-process).
3. **Fork** the repo and create a branch off `main`.

## Development setup

Prerequisites:

- **Node.js ≥ 22.12** (the project is tested on Node 22 in CI)
- **pnpm 9** (declared as `packageManager` in `package.json`)
- For recipe work only, you can get away with just pnpm; for frontend work you'll want a
  local editor with TypeScript + ESLint support.

```bash
git clone https://github.com/<your-fork>/vllm-ascend-recipes.git
cd vllm-ascend-recipes
pnpm install
pnpm dev          # http://localhost:4321/vllm-ascend-recipes/
```

> The dev server runs in the foreground by default. To run it detached, use
> `astro dev --background` (then `astro dev status` / `astro dev stop` / `astro dev logs`).
> See [AGENTS.md](AGENTS.md).

Useful scripts (see [`package.json`](package.json) and the [README](README.md#scripts)):

| Command                             | What it does                                                    |
| ----------------------------------- | --------------------------------------------------------------- |
| `pnpm dev`                          | Start the Astro dev server                                      |
| `pnpm build`                        | Produce a static build in `dist/`                               |
| `pnpm validate`                     | Validate every YAML in `models/` against the zod schema         |
| `pnpm typecheck`                    | Run `astro check` (Astro + TypeScript)                          |
| `pnpm lint` / `pnpm lint:fix`       | ESLint (flat config)                                            |
| `pnpm format` / `pnpm format:check` | Prettier                                                        |
| `./scripts/format.sh`               | Run validate + typecheck + lint + format:check — **mirrors CI** |
| `./scripts/format.sh --fix`         | Auto-fix formatting + lint, then re-check                       |

`./scripts/format.sh` runs the exact same four steps CI runs — use it locally before pushing.

---

## Adding or updating a recipe

Recipes live as structured YAML under `models/en/{Provider}/{Model-Name}.yaml`. The URL is
derived from the path, e.g. `models/en/Qwen/Qwen3-30B-A3B.yaml` → `/qwen/qwen3-30b-a3b`.

### Quick loop

1. **Copy a template.** Use an existing recipe (`models/en/Qwen/Qwen3-30B-A3B.yaml`) as a
   starting point.
2. **Fill in the YAML** following the [schema](README.md#yaml-schema). The authoritative
   schema lives in [`src/lib/schema.ts`](src/lib/schema.ts); the README's
   [YAML schema](README.md#yaml-schema) section is the practical reference.
3. **Validate.**

   ```bash
   pnpm validate
   ```

   This fails fast on any schema error.

4. **Preview.**

   ```bash
   pnpm dev
   ```

   Open the page at `/{provider}/{model}` and check the cascade selector, command block,
   and badges render correctly.

5. **Translate** to `models/zh/{Provider}/{Model-Name}.yaml` (see below).
6. **Run the full gate.**

   ```bash
   ./scripts/format.sh
   ```

### What makes a good recipe

- **Reproducible.** The `vllm serve` command in `scenarios[].steps[].content` must be
  copy-paste runnable on the stated NPU SKU + precision.
- **Verified hardware.** Set `meta.hardware` truthfully: `verified` (you ran it
  end-to-end), `experimental` (works but not fully validated), or `unsupported`.
- **Official sources only.** `weight_download` and `references` should link to the model's
  official HuggingFace / ModelScope / Modelers card — never to personal mirrors.
- **No secrets.** Commands and links must not contain tokens, passwords, or credentials.
- **Minimal flags.** Only include flags that matter for this deployment. Don't dump every
  possible option.

> If upstream model support is still experimental and you can't fill the full recipe, keep
> `weight_download` and `scenarios` minimal and mark `meta.hardware` as `experimental`.
> A stub recipe is better than no recipe.

---

## Translating recipes (i18n)

- **English is the source of truth.** Developers maintain `models/en/`.
- **Chinese lives in `models/zh/`** with the same directory + filename structure. If a
  recipe is missing in `zh/`, the site silently falls back to the English content, but CI
  will warn about unpaired files.
- Translate the markdown bodies wholesale (`overview`, `tuning`, `faq`, scenario step
  text, etc.). Keep `model_id`, commands, flags, and URLs **identical** across languages —
  only prose changes.
- UI strings live in [`src/lib/i18n.ts`](src/lib/i18n.ts).

When adding a recipe, always create both `models/en/...` and `models/zh/...` in the same PR.

---

## Contributing to the site / frontend

The site is **Astro 7 + React 19 + Tailwind v4**, built as a static site. See the
[project layout](README.md#project-layout) in the README for the directory map.

Guidelines:

- Prefer **existing components** in `src/components/` before adding new ones. Look at how
  `CascadeSelector.tsx`, `ModelCard.astro`, `MarkdownContent.astro` work.
- Keep components **typed** — the zod schema in `src/lib/schema.ts` is the single source of
  truth; derive types from it (`src/lib/types.ts`).
- **Don't introduce a new framework** (Vue, Svelte, etc.) without opening an RFC first.
- Styling: use Tailwind utility classes and the design tokens in
  `src/styles/global.css`. Don't add a second CSS approach.
- Client-side interactivity goes in React islands; static content stays in `.astro`.

---

## Coding standards

CI (`.github/workflows/lint.yml` and `pr-recipe-verify.yml`) runs four checks. They must
all pass before a PR can be merged:

1. **YAML validation** — `pnpm validate`
2. **Type check** — `pnpm typecheck`
3. **ESLint** — `pnpm lint`
4. **Prettier** — `pnpm format:check`

Run them all locally with:

```bash
./scripts/format.sh            # check only
./scripts/format.sh --fix      # auto-fix, then re-check
```

Additional notes:

- Line endings: the repo uses LF. Configure git `core.autocrlf=input` on Windows.
- Don't commit generated files (`dist/`, lockfile drift). `pnpm-lock.yaml` changes must
  come from `pnpm install`, not manual edits.
- Keep PRs focused — one recipe or one feature per PR makes review much faster.

---

## Commit messages and DCO sign-off

### Sign off your commits (DCO)

This project follows the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO), the same as vLLM-Ascend. Every commit must be signed off to certify that you wrote
it or otherwise have the right to submit it.

Sign off by adding `-s` when committing:

```bash
git commit -s -m "docs(recipe): add Qwen3-30B-A3B W8A8 recipe"
```

The commit must contain a line like:

```text
Signed-off-by: Your Name <your.email@example.com>
```

> `git config --global user.name` / `user.email` must match the `Signed-off-by` identity.

### Commit message style

- Use the imperative mood: _"Add recipe"_, not _"Added recipe"_.
- Keep the subject line ≤ ~72 characters.
- Reference the issue/PR number in the body when relevant (`Fixes #123`).
- One logical change per commit.

This project uses [Conventional Commits](https://www.conventionalcommits.org/). Prefix the
subject with a type and an optional scope, e.g. `docs(recipe): add Qwen3-30B-A3B W8A8 recipe`.

Common types:

| Type       | Use for                                           |
| ---------- | ------------------------------------------------- |
| `feat`     | A new recipe or a new site feature                |
| `fix`      | A bug fix in a recipe or the site                 |
| `docs`     | Docs, CONTRIBUTING, README, recipe prose          |
| `style`    | Formatting-only changes (Prettier, lint auto-fix) |
| `refactor` | Code restructuring with no behavior change        |
| `i18n`     | `models/zh/` translation work                     |
| `ci`       | CI / GitHub Actions workflows                     |
| `chore`    | Tooling, deps, chores                             |

---

## Pull request workflow

1. **Fork & branch.** Create a branch off `main`:

   ```bash
   git checkout -b add-qwen3-30b-recipe main
   ```

2. **Make your changes.** Add/edit the YAML (and its `zh/` mirror) or site code.
3. **Run the gate locally.**

   ```bash
   ./scripts/format.sh
   ```

   All four steps must pass.

4. **Commit with `-s`** (see [DCO](#sign-off-your-commits-dco)).
5. **Push to your fork** and open a PR against `main`.
6. **Fill in the PR template.** State what changed, whether it's user-facing, and how you
   tested it (which NPU / precision if you verified a deployment).
7. **Preview.** Once CI finishes, a bot posts a **Netlify preview URL** as a comment on
   the PR. Use it to eyeball the rendered page. (Preview artifacts expire in 3 days.)
8. **Address review feedback.** Push follow-up commits — avoid force-pushing unless a
   reviewer asks for a rebase.
9. **Merge.** A maintainer will merge once CI is green and review is approved. We typically
   use squash-merge to keep history clean.

### CI on PRs

- [`lint.yml`](.github/workflows/lint.yml) — validate + typecheck + lint + format:check
  (split by changed paths: `models/`, `src/`, `.github/workflows/`).
- [`pr-recipe-verify.yml`](.github/workflows/pr-recipe-verify.yml) — YAML validation, i18n
  pairing check, full site build, and (for whitelisted recipes) an end-to-end deployment
  verification on real Ascend NPU hardware.
- [`preview-build.yml`](.github/workflows/preview-build.yml) + `preview-deploy.yml` — the
  Netlify preview comment.

---

## RFC process

An **RFC** (Request for Comments) is for **non-trivial, cross-cutting** changes where you
want community alignment before building. Examples:

- A new top-level YAML schema field or a breaking change to an existing field.
- Redesigning the cascade selector or the recipe taxonomy.
- A new i18n language or a change to the i18n strategy.
- Introducing a new framework, a new build system, or a major dependency.

**You do not need an RFC** for normal recipe additions, bug fixes, translations, or
small UI tweaks — just open a PR.

To start an RFC:

1. Open an issue using the **💬 Request for comments (RFC)** template
   (`.github/ISSUE_TEMPLATE/750-RFC.yml`).
2. Fill in **Motivation**, **Proposed change**, and **Feedback period** (usually ≥ 1 week).
3. Link related issues / discussions.
4. After the feedback period, a maintainer will summarize the outcome and either approve
   it for implementation (open a PR referencing the RFC issue) or close it.

Browse [previous RFCs](https://github.com/vllm-ascend/vllm-ascend-recipes/issues?q=label%3ARFC+sort%3Aupdated-desc)
for reference.

---

## Reporting bugs and requesting recipes

Pick the matching issue template (`.github/ISSUE_TEMPLATE/`):

| Template                      | Use for                                                          |
| ----------------------------- | ---------------------------------------------------------------- |
| 📚 Documentation              | Wrong/outdated content on a recipe page or the site              |
| 💻 Usage                      | You're following a recipe and got stuck                          |
| 🐛 Bug report                 | Broken page, wrong command, dead link, schema error, site UI bug |
| 🚀 Feature request            | A new site feature or tooling improvement                        |
| 🤗 Request a new model recipe | You want a recipe for a model that isn't on the site             |
| 💬 RFC                        | A non-trivial design change (see [RFC process](#rfc-process))    |
| 🎲 Others                     | Anything else                                                    |

> For the vLLM-Ascend **engine**, open issues in
> [vllm-project/vllm-ascend](https://github.com/vllm-project/vllm-ascend/issues), not here.
> For open-ended questions, [Discussions](https://github.com/vllm-ascend/vllm-ascend-recipes/discussions)
> usually gets a faster response.

---

## License

By contributing, you agree that your contributions will be licensed under the
**Apache License 2.0**. See the [LICENSE](LICENSE) file.

```text
Copyright 2025 The vLLM Ascend Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

Thanks for contributing to vLLM-Ascend Recipes! 🎉
