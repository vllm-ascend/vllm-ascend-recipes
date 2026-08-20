#!/usr/bin/env node
/**
 * Recipe field-level validation beyond the zod schema.
 *
 * `pnpm validate` (scripts/validate-yaml.ts) checks types/format via zod.
 * This script checks cross-field / upstream-alignment invariants that the
 * schema cannot express, and reports missing or inconsistent fields per
 * recipe so a PR fails fast before it reaches the cluster:
 *
 *   1. required top-level sections (meta / model / overview /
 *      weight_download / env_setup / scenarios / references)
 *   2. upstream-aligned fields (model_id, variants/default precision,
 *      features args-or-env, opt_in_features ⊆ features, config_params
 *      types, strategy interlock, guide "", docker install)
 *   3. scenario step fields (npu/precision/deployment/case, steps,
 *      {{name}} and %%CONFIG%% resolution, script-backed contract)
 *   4. en/zh structural parity
 *
 * Usage: npx tsx scripts/validate-recipe-fields.ts
 */

import { parse } from 'yaml';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

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

function keys(o: unknown, p = ''): string[] {
  const out: string[] = [];
  if (Array.isArray(o)) {
    o.forEach((v, i) => out.push(...keys(v, `${p}[${i}]`)));
  } else if (o && typeof o === 'object') {
    for (const [k, v] of Object.entries(o as AnyRecord)) {
      out.push(...keys(v, p ? `${p}.${k}` : k));
    }
  } else {
    out.push(p);
  }
  return out;
}

function missing(obj: AnyRecord, required: string[]): string[] {
  return required.filter((k) => {
    const v = obj[k];
    return v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
  });
}

function checkRecipe(file: string, data: AnyRecord): string[] {
  const errors: string[] = [];

  // ---- 1. Required top-level sections ----
  const topMissing = missing(data, [
    'meta',
    'model',
    'overview',
    'weight_download',
    'env_setup',
    'scenarios',
    'references',
  ]);
  if (topMissing.length) {
    errors.push(`missing top-level fields: ${topMissing.join(', ')}`);
  }

  // ---- 2. meta ----
  const meta = (data.meta ?? {}) as AnyRecord;
  const metaMissing = missing(meta, ['title', 'slug', 'provider', 'description', 'date_added']);
  if (metaMissing.length) {
    errors.push(`meta missing: ${metaMissing.join(', ')}`);
  }

  // ---- 3. model / upstream-aligned fields ----
  const model = (data.model ?? {}) as AnyRecord;
  const modelMissing = missing(model, [
    'model_id',
    'architecture',
    'parameter_count',
    'context_length',
    'modality',
  ]);
  if (modelMissing.length) {
    errors.push(`model missing: ${modelMissing.join(', ')}`);
  }
  if (typeof model.model_id === 'string' && model.model_id && !model.model_id.includes('/')) {
    errors.push(`model.model_id "${model.model_id}" should be "<org>/<name>"`);
  }
  if (model.architecture !== undefined && !['dense', 'moe'].includes(String(model.architecture))) {
    errors.push(`model.architecture must be dense|moe, got "${model.architecture}"`);
  }

  // variants: default required; every precision non-empty
  const variants = (data.variants ?? {}) as AnyRecord;
  if (Object.keys(variants).length) {
    if (!variants.default) {
      errors.push('variants present but missing "default" variant');
    }
    for (const [name, v] of Object.entries(variants)) {
      const vv = (v ?? {}) as AnyRecord;
      if (!vv.precision) {
        errors.push(`variants.${name} missing precision`);
      }
    }
  }

  // features: each needs args or env
  const features = (data.features ?? {}) as AnyRecord;
  for (const [name, f] of Object.entries(features)) {
    const ff = (f ?? {}) as AnyRecord;
    if (!ff.args && !ff.env) {
      errors.push(`features.${name} must declare args or env`);
    }
  }

  // opt_in_features ⊆ features
  const optIn = (data.opt_in_features ?? []) as string[];
  for (const k of optIn) {
    if (!features[k]) {
      errors.push(`opt_in_features references undeclared feature "${k}"`);
    }
  }

  // config_params: type must be number|string|bool
  const configParams = (data.config_params ?? {}) as AnyRecord;
  for (const [name, cp] of Object.entries(configParams)) {
    const t = (cp as AnyRecord).type;
    if (t !== undefined && !['number', 'string', 'bool'].includes(String(t))) {
      errors.push(`config_params.${name}.type must be number|string|bool, got "${t}"`);
    }
  }

  // strategy interlock
  const strategies = (data.compatible_strategies ?? []) as string[];
  for (const s of (data.scenarios ?? []) as AnyRecord[]) {
    const st = s.strategy as string | undefined;
    if (st && strategies.length && !strategies.includes(st)) {
      errors.push(`scenario strategy "${st}" not in compatible_strategies`);
    }
  }

  // Ascend conventions
  if ('guide' in data && data.guide !== '') {
    errors.push('guide should be "" (tutorial content lives in our fields)');
  }
  const install = (model.install ?? {}) as AnyRecord;
  if (Object.keys(install).length && install.pip !== false) {
    errors.push('model.install.pip should be false (Ascend is docker-only)');
  }

  // ---- 4. Scenario / step fields ----
  const scenarios = (data.scenarios ?? []) as AnyRecord[];
  const extraConfig = ((data.extra_config ?? []) as AnyRecord[]).map((x) => x.key);
  const featureKeys = new Set(Object.keys(features));
  const configKeys = new Set(Object.keys(configParams));
  scenarios.forEach((s, i) => {
    const tag = `scenarios[${i}]`;
    const scMissing = missing(s, ['npu', 'precision', 'deployment', 'case']);
    if (scMissing.length) {
      errors.push(`${tag} missing: ${scMissing.join(', ')}`);
    }
    const steps = (s.steps ?? []) as AnyRecord[];
    if (!steps.length) {
      errors.push(`${tag} has no steps`);
    }
    steps.forEach((st, j) => {
      if (!st.title || !st.content) {
        errors.push(`${tag}.steps[${j}] missing title or content`);
      }
      const content = String(st.content ?? '');
      for (const m of content.matchAll(/\{\{(\w+)\}\}/g)) {
        const name = m[1];
        // Scenario-level config_params override/extend the top-level set.
        const scenarioConfigs = new Set(Object.keys((s.config_params ?? {}) as AnyRecord));
        if (!configKeys.has(name) && !scenarioConfigs.has(name) && !featureKeys.has(name)) {
          errors.push(`${tag}.steps[${j}] placeholder {{${name}}} is not a config_params/feature`);
        }
      }
      for (const m of content.matchAll(/%%CONFIG:([\w-]+)%%/g)) {
        const key = m[1];
        if (!extraConfig.includes(key) && !featureKeys.has(key)) {
          errors.push(`${tag}.steps[${j}] %%CONFIG:${key}%% has no extra_config/feature`);
        }
      }
    });

    // script-backed contract
    if (s.scripts !== undefined) {
      const scripts = (s.scripts ?? {}) as AnyRecord;
      const scMissing2 = missing(s, ['test_id', 'npu_per_node']);
      if (scMissing2.length) {
        errors.push(`${tag} script-backed missing: ${scMissing2.join(', ')}`);
      }
      if (!scripts['service-check']) {
        errors.push(`${tag} script-backed requires a service-check script`);
      }
      for (const st of steps) {
        for (const m of String(st.content ?? '').matchAll(/\{\{script:([^{}]+)\}\}/g)) {
          if (!scripts[m[1]]) {
            errors.push(`${tag} references unknown script "${m[1]}"`);
          }
        }
      }
    }
  });

  // weight_download non-empty with sources
  const weightDownload: AnyRecord[] = Array.isArray(data.weight_download)
    ? (data.weight_download as AnyRecord[])
    : [];
  if (
    weightDownload.length &&
    weightDownload.some((w) => {
      const src = w.sources;
      return !Array.isArray(src) || src.length === 0;
    })
  ) {
    errors.push('weight_download entries must have non-empty sources');
  }

  return errors;
}

const enDir = new URL('../models/en/', import.meta.url).pathname;
const zhDir = new URL('../models/zh/', import.meta.url).pathname;
const enFiles = findYamlFiles(enDir);
const zhFiles = new Set(findYamlFiles(zhDir).map((p) => p.replace(/\/zh\//, '/en/')));

let hasErrors = false;
let checked = 0;

for (const file of enFiles) {
  const raw = readFileSync(file, 'utf-8');
  const data = parse(raw) as AnyRecord;
  const errors = checkRecipe(file, data);

  // en/zh structural parity
  const zhFile = file.replace(/\/en\//, '/zh/');
  if (zhFiles.has(file)) {
    if (zhFile && findYamlFiles(zhDir).includes(zhFile.replace(/\/en\//, '/zh/'))) {
      const zh = parse(readFileSync(zhFile, 'utf-8')) as AnyRecord;
      const enKeys = keys(data);
      const zhKeys = keys(zh);
      if (JSON.stringify(enKeys.sort()) !== JSON.stringify(zhKeys.sort())) {
        errors.push('en/zh field structure differs');
      }
    }
  }

  checked++;
  if (errors.length) {
    hasErrors = true;
    console.error(` FAIL  ${file}`);
    for (const e of errors) {
      console.error(`       - ${e}`);
    }
  } else {
    console.log(`  OK  ${file}`);
  }
}

console.log(`\n ${checked} recipe(s) field-checked.`);
if (hasErrors) {
  console.error(' Recipe field validation failed. Fix the errors above.');
  process.exit(1);
}
console.log(' All recipe fields passed validation.');
