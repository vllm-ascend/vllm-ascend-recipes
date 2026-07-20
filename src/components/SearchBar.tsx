import { useState, useMemo } from 'react';
import { useLang } from '../lib/useLang';

interface ModelItem {
  hf_id: string;
  title: string;
  provider: string;
  description: string;
  architecture: string;
  parameters: string;
  active_parameters: string | null;
  context_length: number;
  modality: string;
  url: string;
  npus: string[];
  precisions: string[];
  deployments: string[];
}

interface SearchBarProps {
  modelsEn: ModelItem[];
  modelsZh: ModelItem[];
}

export default function SearchBar({ modelsEn, modelsZh }: SearchBarProps) {
  const { lang, t } = useLang();
  const models = lang === 'zh' ? modelsZh : modelsEn;
  const [query, setQuery] = useState('');
  const [filterNpu, setFilterNpu] = useState('');
  const [filterArch, setFilterArch] = useState('');
  const [filterModality, setFilterModality] = useState('');

  const allNpus = useMemo(() => {
    const set = new Set<string>();
    models.forEach((m) => m.npus.forEach((n) => set.add(n)));
    return Array.from(set).sort();
  }, [models]);

  const allArchitectures = useMemo(() => {
    const set = new Set<string>();
    models.forEach((m) => set.add(m.architecture));
    return Array.from(set).sort();
  }, [models]);

  const allModalities = useMemo(() => {
    const set = new Set<string>();
    models.forEach((m) => set.add(m.modality));
    return Array.from(set).sort();
  }, [models]);

  const filtered = useMemo(() => {
    return models.filter((m) => {
      if (query) {
        const q = query.toLowerCase();
        if (
          !m.title.toLowerCase().includes(q) &&
          !m.hf_id.toLowerCase().includes(q) &&
          !m.provider.toLowerCase().includes(q) &&
          !m.description.toLowerCase().includes(q)
        ) {
          return false;
        }
      }
      if (filterNpu && !m.npus.includes(filterNpu)) return false;
      if (filterArch && m.architecture !== filterArch) return false;
      if (filterModality && m.modality !== filterModality) return false;
      return true;
    });
  }, [models, query, filterNpu, filterArch, filterModality]);

  const selectClass =
    'px-3 py-2 text-xs font-mono rounded-lg border border-ink-800/60 bg-ink-900/40 text-ink-300 focus:outline-none focus:border-accent-500/40 focus:ring-1 focus:ring-accent-500/20 cursor-pointer transition-colors';

  return (
    <div>
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="relative flex-1">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            placeholder={t('searchPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 text-sm font-mono rounded-lg border border-ink-800/60 bg-ink-900/40 text-ink-200 placeholder:text-ink-600 focus:outline-none focus:border-accent-500/40 focus:ring-1 focus:ring-accent-500/20 transition-colors"
          />
        </div>
        <select
          value={filterNpu}
          onChange={(e) => setFilterNpu(e.target.value)}
          className={selectClass}
        >
          <option value="">{t('filterAllNpu')}</option>
          {allNpus.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <select
          value={filterArch}
          onChange={(e) => setFilterArch(e.target.value)}
          className={selectClass}
        >
          <option value="">{t('filterAllArch')}</option>
          {allArchitectures.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          value={filterModality}
          onChange={(e) => setFilterModality(e.target.value)}
          className={selectClass}
        >
          <option value="">{t('filterAllModality')}</option>
          {allModalities.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      <p className="text-xs font-mono text-ink-600 mb-4">
        <span className="text-accent-400">{filtered.length}</span> {t('totalModels')}
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map((m) => (
          <a
            key={m.hf_id}
            href={m.url}
            className="group relative block p-5 rounded-lg border border-ink-800/60 hover:border-accent-500/30 bg-ink-900/40 hover:bg-ink-900/60 transition-all duration-200 overflow-hidden"
          >
            <div className="absolute top-0 left-0 w-full h-px bg-gradient-to-r from-transparent via-accent-500/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <div className="flex items-start justify-between mb-2">
              <h3 className="font-display font-semibold text-sm text-ink-100 group-hover:text-accent-300 transition-colors tracking-tight">
                {m.title}
              </h3>
              <span className="text-[10px] font-mono uppercase tracking-wider text-ink-500 px-1.5 py-0.5 rounded border border-ink-700/50">
                {m.architecture}
              </span>
            </div>
            <p className="text-xs text-ink-500 line-clamp-2 mb-3 leading-relaxed">
              {m.description}
            </p>
            <div className="flex flex-wrap gap-1.5">
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-accent-500/10 text-accent-400 border border-accent-500/15">
                {m.active_parameters ? `${m.parameters}/${m.active_parameters}` : m.parameters}
              </span>
              {m.npus.slice(0, 2).map((n) => (
                <span
                  key={n}
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-400 border border-sky-500/15"
                >
                  {n}
                </span>
              ))}
            </div>
          </a>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16">
          <p className="font-mono text-sm text-ink-600">{t('noResults')}</p>
        </div>
      )}
    </div>
  );
}
