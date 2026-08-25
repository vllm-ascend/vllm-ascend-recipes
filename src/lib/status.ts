/**
 * Verification status types & helpers.
 *
 * `status/*.json` files live in the `gh-pages` branch (published by
 * .github/workflows/publish-status.yml). The Astro site is SSG, so we don't
 * know the status at build time — it is loaded client-side by <VerifyBadge>.
 *
 * Schema mirrors what publish-status.yml emits.
 */

export type RunKind = 'pr' | 'nightly' | 'manual';
export type RunConclusion = 'success' | 'failure' | 'cancelled' | 'skipped';

export interface RunStatus {
  kind: RunKind;
  /** "pass" | "fail" | "skip" | "unknown" — one-word status file content. */
  status: 'pass' | 'fail' | 'skip' | 'unknown' | string;
  /** Workflow conclusion — "success" / "failure" / "cancelled" / "skipped". */
  conclusion: RunConclusion | string;
  head_sha: string;
  head_sha_url: string;
  workflow_run_id: number;
  workflow_run_url: string;
  recipe_path: string;
  recipe_yaml_url: string;
  /** Hyperlink target: the human-readable params.json in gh-pages. */
  params_url: string;
  started_at: string;
  finished_at: string;

  // PR-specific (null for nightly)
  pr_number: number | null;
  pr_url: string | null;
  pr_title: string | null;
  pr_author: string | null;
}

export interface ScenarioSelector {
  test_id?: string;
  npu: string;
  precision: string;
  deployment: string;
  case: string;
}

export interface VerificationTargetStatus {
  test_id?: string;
  selector: ScenarioSelector;
  runner: string;
  mode: string;
  last_pr_run: RunStatus | null;
  last_nightly_run: RunStatus | null;
  /** Latest manually-dispatched run; not displayed as PR/nightly evidence. */
  last_manual_run?: RunStatus | null;
  /** All exact PR/nightly evidence retained for the selected configuration. */
  history?: RunStatus[];
}

export interface ModelStatus {
  model: string;
  last_pr_run: RunStatus | null;
  last_nightly_run: RunStatus | null;
  last_manual_run?: RunStatus | null;
  /** Explicitly-approved, configuration-level verification results. */
  targets?: Record<string, VerificationTargetStatus>;
}

export interface StatusIndex {
  models: { slug: string }[];
}

// Status files are served at the GitHub Pages origin in production. We
// hard-code the site origin (matches astro.config.mjs `site:`) so the badge
// works from any page. In dev mode we use a relative URL prefixed with the
// configured BASE_URL (matches astro.config.mjs `base:`) so it resolves
// against `localhost:4321/<BASE_URL>/status/<slug>.json` and picks up the
// mock fixtures under `public/status/` for an instant visual preview.
export const STATUS_ORIGIN = 'https://vllm-ascend.github.io/vllm-ascend-recipes';

interface ImportMetaWithEnv {
  readonly env?: Record<string, unknown>;
}
const metaEnv = (import.meta as unknown as ImportMetaWithEnv).env ?? {};
const env = metaEnv as Record<string, string | boolean | undefined>;
const baseUrl = (env.BASE_URL as string | undefined) ?? '/';

// Always include the site's BASE_URL prefix. Astro copies `public/status/`
// verbatim into the build output, so this resolves correctly in:
//   - dev:        localhost:4321/<base>/status/<slug>.json  (mock in public/)
//   - PR preview: <hash>.netlify.app/<base>/status/<slug>.json  (mock copied to dist/)
//   - production: <site>/<base>/status/<slug>.json  (real status published to gh-pages branch)
// baseUrl ends with a slash already (e.g. "/vllm-ascend-recipes/"); strip it
// so the URL starts with "/<base>" without a double slash.
function basePath(): string {
  return baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
}

export function statusUrlForSlug(slug: string): string {
  return `${basePath()}/status/${slug}.json`;
}

/**
 * Netlify deploy previews are served from `/`, while GitHub Pages uses the
 * configured Astro base path. Try the configured path first, then the root
 * copy emitted by the preview build.
 */
export function statusUrlCandidatesForSlug(slug: string): string[] {
  return [...new Set([statusUrlForSlug(slug), `/status/${slug}.json`])];
}

/** Fetch a status file across both supported static-site deployment paths. */
export async function fetchModelStatus(slug: string): Promise<ModelStatus | null> {
  for (const url of statusUrlCandidatesForSlug(slug)) {
    try {
      const response = await fetch(url, { cache: 'no-cache' });
      if (response.ok) return (await response.json()) as ModelStatus;
    } catch {
      // Try the next static deployment path.
    }
  }
  return null;
}

export function statusIndexUrl(): string {
  return `${basePath()}/status/index.json`;
}

export function durationHuman(start: string, end: string): string {
  if (!start || !end) return '';
  const s = Date.parse(start);
  const e = Date.parse(end);
  if (Number.isNaN(s) || Number.isNaN(e)) return '';
  const sec = Math.max(0, Math.floor((e - s) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

/**
 * Pick the freshest of two optional run records by `finished_at`. Used by
 * UI surfaces (homepage status dot, badge) to decide which run drives the
 * user-visible state. Returning the freshest — rather than preferring
 * `last_pr_run` whenever it exists — protects against stale `last_pr_run`
 * mock fixtures that survive `publish_skeleton.py`'s `looks_real()`
 * heuristic and would otherwise dominate the displayed status forever.
 */
export function pickFreshestRun(
  a: RunStatus | null | undefined,
  b: RunStatus | null | undefined,
): RunStatus | null {
  if (!a) return b ?? null;
  if (!b) return a;
  const ta = Date.parse(a.finished_at);
  const tb = Date.parse(b.finished_at);
  if (Number.isNaN(ta)) return b;
  if (Number.isNaN(tb)) return a;
  return tb >= ta ? b : a;
}

/** Return the one published target matching all four selector dimensions. */
export function findScenarioTarget(
  status: Pick<ModelStatus, 'targets'> | null | undefined,
  scenario: ScenarioSelector,
): VerificationTargetStatus | null {
  if (!status?.targets) return null;
  return (
    Object.values(status.targets).find((target) => {
      if (scenario.test_id && target.test_id) return target.test_id === scenario.test_id;
      return (
        target.selector.npu === scenario.npu &&
        target.selector.precision === scenario.precision &&
        target.selector.deployment === scenario.deployment &&
        target.selector.case === scenario.case
      );
    }) ?? null
  );
}

/** Production verification is true only when the latest main nightly passed. */
export function isNightlyVerified(target: VerificationTargetStatus | null | undefined): boolean {
  return target?.last_nightly_run?.status === 'pass';
}

export type ModelVerificationSummary = 'all-pass' | 'partial-pass' | 'no-pass' | 'untracked';

/** Summarize the latest nightly result for every allowlisted configuration. */
export function summarizeModelVerification(
  status: Pick<ModelStatus, 'targets'> | null | undefined,
): ModelVerificationSummary {
  const targets = Object.values(status?.targets ?? {});
  if (targets.length === 0) return 'untracked';
  const passed = targets.filter((target) => isNightlyVerified(target)).length;
  if (passed === targets.length) return 'all-pass';
  if (passed > 0) return 'partial-pass';
  return 'no-pass';
}
