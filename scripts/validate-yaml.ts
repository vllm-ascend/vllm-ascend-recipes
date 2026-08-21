import { parse } from 'yaml';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { modelSchema } from '../src/lib/schema';

type AnyRecord = Record<string, unknown>;

function findYamlFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      results.push(...findYamlFiles(path));
    } else if (entry.endsWith('.yaml') || entry.endsWith('.yml')) {
      results.push(path);
    }
  }
  return results;
}

/** Collect every script-backed scenario (scenario.scripts !== undefined). */
function collectScriptBacked(data: AnyRecord): AnyRecord[] {
  const scenarios = (data.scenarios as AnyRecord[] | undefined) ?? [];
  return scenarios.filter((s) => s.scripts !== undefined);
}

/** Extract shell env-var names (`$FOO` / `${FOO}`) from a script/step body. */
function envVarsOf(text: string): Set<string> {
  const out = new Set<string>();
  for (const m of text.matchAll(/\$(\{)?([A-Z][A-Z0-9_]*)(\})?/g)) {
    out.add(m[2]);
  }
  return out;
}

/** Maximum node index referenced by `<ROLE>_NODE_<i>_*` vars + 1; 0 if none. */
function nodeCountForRole(vars: Set<string>, role: string): number {
  const re = new RegExp(`^${role}_NODE_(\\d+)_`);
  let max = -1;
  for (const v of vars) {
    const m = v.match(re);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max + 1;
}

/** Parse `<n>p<m>d` / `<n>-node` case into node counts. */
function caseTopology(
  deployment: string,
  caseName: string,
): { prefill: number; decode: number; nodes: number } | null {
  if (deployment === 'pd') {
    const m = caseName.match(/^(\d+)p(\d+)d$/);
    return m ? { prefill: Number(m[1]), decode: Number(m[2]), nodes: 0 } : null;
  }
  if (deployment === 'non-pd') {
    const m = caseName.match(/^(\d+)-node$/);
    return m ? { prefill: 0, decode: 0, nodes: Number(m[1]) } : null;
  }
  return null;
}

/** npu_per_node consistency with the launch parallelism (external/internal DP). */
function parallelNpuPerNode(scripts: AnyRecord): number | null {
  for (const s of Object.values(scripts)) {
    const content = (s as AnyRecord).content as string;
    // external DP: launch_online_dp.py --dp-size-local X --tp-size Y (any order)
    let m = content.match(
      /--dp-size-local\s+(\d+)[\s\S]*?--tp-size\s+(\d+)|--tp-size\s+(\d+)[\s\S]*?--dp-size-local\s+(\d+)/,
    );
    if (m) {
      const local = m[1] ?? m[4];
      const tp = m[2] ?? m[3];
      if (local !== undefined && tp !== undefined) {
        return Number(local) * Number(tp);
      }
    }
    // internal DP: vllm serve --data-parallel-size-local X --tensor-parallel-size Y
    m = content.match(
      /--data-parallel-size-local\s+(\d+)[\s\S]*?--tensor-parallel-size\s+(\d+)|--tensor-parallel-size\s+(\d+)[\s\S]*?--data-parallel-size-local\s+(\d+)/,
    );
    if (m) {
      const local = m[1] ?? m[4];
      const tp = m[2] ?? m[3];
      if (local !== undefined && tp !== undefined) {
        return Number(local) * Number(tp);
      }
    }
  }
  return null;
}

/** Semantic checks beyond the zod schema (cross-field / cross-file). */
function validateMultiNodeSemantics(
  file: string,
  data: AnyRecord,
  seenTestIds: Map<string, string>,
): string[] {
  const errors: string[] = [];
  for (const scenario of collectScriptBacked(data)) {
    const deployment = scenario.deployment as string;
    const caseName = scenario.case as string;
    const testId = scenario.test_id as string | undefined;
    const npuPerNode = scenario.npu_per_node as number | undefined;
    const scripts = (scenario.scripts ?? {}) as AnyRecord;

    // test_id must be globally unique across en recipes (converter target).
    if (testId !== undefined) {
      const owner = seenTestIds.get(testId);
      if (owner && owner !== file) {
        errors.push(`test_id "${testId}" is duplicated in ${owner} and ${file}`);
      } else {
        seenTestIds.set(testId, file);
      }
    }

    // pd scenarios need both prefill and decode role scripts so the runtime
    // can assign roles; non-pd needs at least one service script besides
    // service-check.
    const keys = Object.keys(scripts).filter((k) => k !== 'service-check');
    if (deployment === 'pd') {
      if (!keys.some((k) => k.includes('prefill')) || !keys.some((k) => k.includes('decode'))) {
        errors.push(
          `scenario ${testId ?? caseName}: pd scripts need prefill and decode role scripts`,
        );
      }
    } else if (deployment === 'non-pd' && keys.length === 0) {
      errors.push(`scenario ${testId ?? caseName}: non-pd needs at least one service script`);
    }

    // Runtime env vars must match the case topology.
    const topology = caseTopology(deployment, caseName);
    const body = [
      ...Object.values(scripts).map((s) => (s as AnyRecord).content as string),
      ...(scenario.steps as AnyRecord[]).map((st) => st.content as string),
    ].join('\n');
    const vars = envVarsOf(body);
    if (topology) {
      if (deployment === 'pd') {
        const p = nodeCountForRole(vars, 'PREFILL');
        const d = nodeCountForRole(vars, 'DECODE');
        if (p !== topology.prefill) {
          errors.push(
            `scenario ${testId ?? caseName}: PREFILL_NODE_* vars reference ${p} node(s), ` +
              `but case "${caseName}" declares ${topology.prefill}`,
          );
        }
        if (d !== topology.decode) {
          errors.push(
            `scenario ${testId ?? caseName}: DECODE_NODE_* vars reference ${d} node(s), ` +
              `but case "${caseName}" declares ${topology.decode}`,
          );
        }
      } else if (deployment === 'non-pd') {
        // Headless nodes reuse the API node address, so the env-var index
        // cannot count nodes. Derive the node count from internal DP
        // parallelism (size ÷ size-local); fall back to a presence check.
        const api = nodeCountForRole(vars, 'API');
        const headless = nodeCountForRole(vars, 'HEADLESS');
        const dpNodeCount = (() => {
          for (const s of Object.values(scripts)) {
            const content = (s as AnyRecord).content as string;
            const m = content.match(
              /--data-parallel-size\s+(\d+)[\s\S]*?--data-parallel-size-local\s+(\d+)|--data-parallel-size-local\s+(\d+)[\s\S]*?--data-parallel-size\s+(\d+)/,
            );
            if (m) {
              const size = Number(m[1] ?? m[4]);
              const local = Number(m[2] ?? m[3]);
              if (local > 0 && size % local === 0) return size / local;
            }
          }
          return null;
        })();
        const n = dpNodeCount ?? Math.max(api, headless);
        if (n !== topology.nodes) {
          errors.push(
            `scenario ${testId ?? caseName}: internal-DP parallelism implies ${n} node(s), ` +
              `but case "${caseName}" declares ${topology.nodes}`,
          );
        }
        if (api === 0 && headless === 0) {
          errors.push(
            `scenario ${testId ?? caseName}: no API/HEADLESS_NODE_* runtime variable found`,
          );
        }
      }
    }

    // npu_per_node must agree with the launch parallelism when it can be
    // statically derived; otherwise warn (not fail).
    if (npuPerNode !== undefined) {
      const derived = parallelNpuPerNode(scripts);
      if (derived !== null && derived !== npuPerNode) {
        errors.push(
          `scenario ${testId ?? caseName}: npu_per_node=${npuPerNode} disagrees with ` +
            `launch parallelism (${derived} NPUs)`,
        );
      }
    }
  }
  return errors;
}

let hasErrors = false;
const seenTestIds = new Map<string, string>();

for (const lang of ['en', 'zh']) {
  const langDir = new URL(`../models/${lang}/`, import.meta.url).pathname;
  try {
    const files = findYamlFiles(langDir);
    if (files.length === 0) {
      console.warn(` WARN  No YAML files found in models/${lang}/`);
      continue;
    }
    for (const file of files) {
      try {
        const raw = readFileSync(file, 'utf-8');
        const data = parse(raw);
        modelSchema.parse(data);
        if (lang === 'en') {
          for (const err of validateMultiNodeSemantics(file, data as AnyRecord, seenTestIds)) {
            hasErrors = true;
            console.error(` FAIL  [en] ${file}`);
            console.error(`       ${err}`);
          }
        }
        console.log(`  OK  [${lang}] ${file}`);
      } catch (err) {
        hasErrors = true;
        console.error(` FAIL  [${lang}] ${file}`);
        if (err instanceof Error) {
          console.error(`       ${err.message}`);
        }
      }
    }
  } catch {
    console.warn(` WARN  models/${lang}/ directory not found, skipping`);
  }
}

if (hasErrors) {
  console.error('\n YAML validation failed. Fix the errors above.');
  process.exit(1);
} else {
  console.log('\n All YAML files passed validation.');
}
