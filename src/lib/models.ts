import { parse } from 'yaml';
import { basename } from 'node:path';
import type { Model, ModelListItem, ProviderInfo } from './types';
import { modelSchema } from './schema';

const globEn = import.meta.glob('/models/en/**/*.yaml', { query: '?raw', import: 'default' });
const globZh = import.meta.glob('/models/zh/**/*.yaml', { query: '?raw', import: 'default' });

const globMap = { en: globEn, zh: globZh };

function loadGlob(lang: 'en' | 'zh') {
  const globResult = globMap[lang];

  return Promise.all(
    Object.entries(globResult).map(async ([path, loader]) => {
      const raw = (await loader()) as string;
      const data = parse(raw);
      const parsed = modelSchema.parse(data);

      const parts = path.split('/');
      const providerSlug = parts[parts.length - 2];
      const modelSlug = basename(parts[parts.length - 1], '.yaml');

      return {
        ...parsed,
        _provider_slug: providerSlug,
        _model_slug: modelSlug,
        _yaml_path: path,
      } as Model;
    }),
  );
}

export async function getAllModels(lang: 'en' | 'zh' = 'en'): Promise<Model[]> {
  const entries = await loadGlob(lang);
  return entries.sort((a, b) => b.updated.localeCompare(a.updated));
}

export async function getModel(
  provider: string,
  model: string,
  lang: 'en' | 'zh' = 'en',
): Promise<Model | null> {
  const all = await getAllModels(lang);
  return (
    all.find(
      (m) =>
        m._provider_slug.toLowerCase() === provider.toLowerCase() &&
        m._model_slug.toLowerCase() === model.toLowerCase(),
    ) ?? null
  );
}

export async function getProviders(lang: 'en' | 'zh' = 'en'): Promise<ProviderInfo[]> {
  const all = await getAllModels(lang);
  const map = new Map<string, ProviderInfo>();

  for (const m of all) {
    const slug = m._provider_slug;
    if (!map.has(slug)) {
      map.set(slug, { name: m.provider, slug, count: 0 });
    }
    map.get(slug)!.count++;
  }

  return Array.from(map.values()).sort((a, b) => b.count - a.count);
}

export async function getModelList(lang: 'en' | 'zh' = 'en'): Promise<ModelListItem[]> {
  const all = await getAllModels(lang);
  return all.map((m) => {
    const npus = [...new Set(m.scenarios.map((s) => s.npu))];
    const precisions = [...new Set(m.scenarios.map((s) => s.precision))];
    const deployments = [...new Set(m.scenarios.map((s) => s.deployment))];

    return {
      hf_id: m.hf_id,
      title: m.title,
      provider: m.provider,
      description: m.description,
      architecture: m.architecture,
      parameters: m.parameters,
      active_parameters: m.active_parameters,
      context_length: m.context_length,
      modality: m.modality,
      updated: m.updated,
      url: `/${m._provider_slug}/${m._model_slug}`,
      json: `/${m._provider_slug}/${m._model_slug}.json`,
      npus,
      precisions,
      deployments,
    };
  });
}

export function formatContextLength(len: number): string {
  if (len >= 1000000) {
    return `${(len / 1000000).toFixed(1).replace(/\.0$/, '')}M ctx`;
  }
  if (len >= 1000) {
    return `${(len / 1000).toFixed(0)}K ctx`;
  }
  return `${len} ctx`;
}

export function formatParameters(params: string, active: string | null): string {
  if (active) {
    return `${params}/${active}`;
  }
  return params;
}
