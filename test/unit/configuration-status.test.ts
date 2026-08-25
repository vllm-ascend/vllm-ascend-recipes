import assert from 'node:assert/strict';
import test from 'node:test';
import {
  findScenarioTarget,
  isNightlyVerified,
  summarizeModelVerification,
  type RunStatus,
  type VerificationTargetStatus,
} from '../../src/lib/status';

function run(status: string, kind: RunStatus['kind'] = 'nightly'): RunStatus {
  return {
    kind,
    status,
    conclusion: status === 'pass' ? 'success' : 'failure',
    head_sha: 'abc123',
    head_sha_url: 'https://example.invalid/commit/abc123',
    workflow_run_id: 1,
    workflow_run_url: 'https://example.invalid/actions/runs/1',
    recipe_path: 'models/en/DeepSeek/DeepSeek-V2-Lite-W8A8.yaml',
    recipe_yaml_url: 'https://example.invalid/recipe.yaml',
    params_url: 'https://example.invalid/params.json',
    started_at: '2026-08-25T00:00:00Z',
    finished_at: '2026-08-25T00:01:00Z',
    pr_number: null,
    pr_url: null,
    pr_title: null,
    pr_author: null,
  };
}

const target: VerificationTargetStatus = {
  test_id: 'dsv2lite-pd-2n2c',
  selector: {
    npu: 'Atlas 800I A2',
    precision: 'bf16',
    deployment: 'Multi-Node',
    case: '2-node',
  },
  runner: 'linux-aarch64-a2b4-1',
  mode: 'multi-node',
  last_pr_run: run('pass', 'pr'),
  last_nightly_run: run('pass'),
};

test('matches verification status to the full selected configuration', () => {
  const result = findScenarioTarget({ targets: { 'qwen-a2-2node': target } }, target.selector);

  assert.equal(result, target);
  assert.equal(
    findScenarioTarget(
      { targets: { 'qwen-a2-2node': target } },
      { ...target.selector, precision: 'W8A8' },
    ),
    null,
  );
});

test('matches the same verified target after the detail page switches to Chinese', () => {
  const result = findScenarioTarget(
    { targets: { 'deepseek-v2-lite-a2-w8a8-1p1d': target } },
    {
      test_id: 'dsv2lite-pd-2n2c',
      npu: 'Atlas 800I A2',
      precision: 'W8A8',
      deployment: '多节点-PD分离',
      case: '1P1D（1个Prefill节点 + 1个Decode节点）',
    },
  );

  assert.equal(result, target);
});

test('requires a successful nightly run before rendering a green dot', () => {
  assert.equal(isNightlyVerified(target), true);
  assert.equal(isNightlyVerified({ ...target, last_nightly_run: run('fail') }), false);
  assert.equal(isNightlyVerified({ ...target, last_nightly_run: null }), false);
  assert.equal(
    isNightlyVerified({ ...target, last_pr_run: run('pass', 'pr'), last_nightly_run: null }),
    false,
  );
});

test('summarizes model targets as all, partial, none, or untracked', () => {
  const passing = { ...target, last_nightly_run: run('pass') };
  const failing = { ...target, last_nightly_run: run('fail') };

  assert.equal(summarizeModelVerification({ targets: { passing } }), 'all-pass');
  assert.equal(summarizeModelVerification({ targets: { passing, failing } }), 'partial-pass');
  assert.equal(summarizeModelVerification({ targets: { failing } }), 'no-pass');
  assert.equal(summarizeModelVerification({ targets: {} }), 'untracked');
  assert.equal(summarizeModelVerification(null), 'untracked');
});
