import { parse } from 'yaml';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { modelSchema } from '../src/lib/schema';

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

let hasErrors = false;

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
