<!--  Thanks for sending a pull request!

BEFORE SUBMITTING, PLEASE READ CONTRIBUTING.md

  * Fork the repo, create a branch off `main`.
  * For recipe changes, edit YAML under `models/en/` (and mirror to `models/zh/`).
  * Run `./scripts/format.sh` locally — all steps must pass before review.
  * Every PR gets a Netlify preview link posted as a comment once CI finishes.

DCO: commits must be signed off (`git commit -s`). See CONTRIBUTING.md › "DCO sign-off".
-->

### What this PR does / why we need it

<!--
- Summarize the change and the motivation. If it fixes an issue, link it: Fixes #<issue>.
- For a new/updated recipe, mention the model id, target NPU, and precision.
-->

### Change type

<!--
Check the relevant boxes. Delete the rest.
-->

- [ ] New recipe (`models/en/**`)
- [ ] Recipe update (command, flags, performance numbers)
- [ ] Translation (`models/zh/**` mirror)
- [ ] Site / frontend (`src/**`, `scripts/**`, config)
- [ ] CI / tooling / docs
- [ ] Other

### Does this PR introduce _any_ user-facing change?

<!--
A user-facing change is anything visible on the live site: a new/changed recipe page,
a new URL, modified `vllm serve` command, changed badges, or UI behavior. Documentation-only
edits inside a recipe are still user-facing.
-->

- [ ] No
- [ ] Yes (describe below)

### How was this patch tested?

<!--
At minimum run `./scripts/format.sh` and paste the result. For recipe changes also run
`pnpm dev` and open the rendered page. If you verified a deployment end-to-end on real
NPU hardware, note the SKU + precision and paste the verification curl output.
-->

- [ ] `./scripts/format.sh` passes (validate + typecheck + lint + format:check)
- [ ] `pnpm dev` — page renders correctly at `/{provider}/{model}`
- [ ] `models/en/` and `models/zh/` are paired (no missing translation)
- [ ] End-to-end deployment verified on NPU (specify below)

NPU / precision (if verified):

```text
# e.g. Atlas 800I A3 / W8A8 / TP=8
```

### Screenshots / preview

<!--
Optional. Paste a screenshot of the rendered page, or rely on the Netlify preview URL
that the bot will post after CI.
-->

### Checklist

<!--
Tick what applies. See CONTRIBUTING.md for details.
-->

- [ ] My commits are signed off (`git commit -s`) — DCO
- [ ] YAML follows the schema in `src/lib/schema.ts`
- [ ] No secrets / credentials in commands or links
- [ ] References point to official model cards / docs
