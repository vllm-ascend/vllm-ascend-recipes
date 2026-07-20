import { useState, useMemo, useEffect } from 'react';
import { useLang } from '../lib/useLang';

interface ExtraConfigItem {
  key: string;
  label: string;
}

interface ScenarioStep {
  title: string;
  content: string;
  config_values?: Record<string, { enabled: string; disabled: string }>;
}

interface Scenario {
  npu: string;
  precision: string;
  deployment: string;
  case: string;
  steps: ScenarioStep[];
  default_configs?: string[];
}

interface CascadeSelectorProps {
  scenariosEn: Scenario[];
  scenariosZh: Scenario[];
  extraConfigEn?: ExtraConfigItem[];
  extraConfigZh?: ExtraConfigItem[];
}

// ---- Markdown renderer ----
function stripRenderMarkers(content: string): string {
  return content
    .replace(/%%CONFIG:\w[\w-]*%%|%%\/CONFIG:\w[\w-]*%%/g, '')
    .replace(/%%HL:\w[\w-]*%%|%%\/HL:\w[\w-]*%%/g, '');
}

function renderMarkdown(md: string): string {
  let html = md;

  // Strip %%CONFIG%% markers (keeping inner content) so SSR always shows full content.
  // applyConfigReplace handles the actual selection-based replacement later.
  html = html.replace(/%%CONFIG:\w[\w-]*%%|%%\/CONFIG:\w[\w-]*%%/g, '');

  const codeBlocks: string[] = [];
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const copyCode = stripRenderMarkers(code).trim();
    const escaped = code
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .trimEnd();
    const idx = codeBlocks.length;
    codeBlocks.push(
      `<div class="code-block group relative"><div class="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity z-10"><button class="copy-btn px-2 py-1 text-[10px] font-mono rounded border border-ink-700 bg-ink-800 text-ink-400 hover:text-accent-400 hover:border-accent-500/30 transition-colors" data-code="${encodeURIComponent(copyCode)}">copy</button></div><pre><code class="language-${lang || 'bash'}">${escaped}</code></pre></div>`,
    );
    return `%%CODEBLOCK_${idx}%%`;
  });

  html = html.replace(/(?:^\|.+\|$\n?)+/gm, (match) => {
    const rows = match.trim().split('\n');
    let tableHtml =
      '<div class="overflow-x-auto rounded-lg border border-ink-800/60 mb-4"><table class="w-full text-sm">';
    let headerDone = false;
    for (const row of rows) {
      const cells = row.split('|').filter((c) => c.trim() !== '');
      if (cells.every((c) => /^:?-{3,}:?$/.test(c.trim()))) {
        headerDone = true;
        continue;
      }
      const cellHtml = cells
        .map((c, i) => {
          const tag = !headerDone && i === 0 ? 'th' : 'td';
          const cls =
            tag === 'th'
              ? 'text-left py-2.5 px-4 font-mono text-xs font-medium text-ink-300 uppercase tracking-wider'
              : 'py-2.5 px-4 text-ink-400 font-mono text-xs';
          return `<${tag} class="${cls}">${c.trim()}</${tag}>`;
        })
        .join('');
      if (!headerDone) {
        tableHtml += `<thead><tr class="border-b border-ink-800/60 bg-ink-900/40">${cellHtml}</tr></thead>`;
        headerDone = true;
      } else {
        tableHtml += `<tbody><tr class="border-b border-ink-800/40 last:border-0 hover:bg-ink-900/30 transition-colors">${cellHtml}</tr></tbody>`;
      }
    }
    tableHtml += '</table></div>';
    return tableHtml;
  });

  html = html.replace(
    /^#### (.+)$/gm,
    '<h4 class="font-display text-sm font-semibold mt-4 mb-2 text-ink-300">$1</h4>',
  );
  html = html.replace(
    /^### (.+)$/gm,
    '<h3 class="font-display text-base font-semibold mt-6 mb-2 text-ink-200">$1</h3>',
  );
  html = html.replace(
    /^## (.+)$/gm,
    '<h2 class="font-display text-lg font-semibold mt-6 mb-3 text-ink-100">$1</h2>',
  );
  html = html.replace(
    /^> (.+)$/gm,
    '<blockquote class="border-l-2 border-accent-500/40 pl-4 py-2 my-4 bg-accent-500/5 rounded-r text-sm text-ink-400">$1</blockquote>',
  );

  const lines = html.split('\n');
  const result: string[] = [];
  let inList = false;
  let paragraphBuf: string[] = [];

  function flushParagraph() {
    if (paragraphBuf.length > 0) {
      result.push(
        `<p class="text-sm text-ink-400 leading-relaxed mb-4">${paragraphBuf.join('<br />\n')}</p>`,
      );
      paragraphBuf = [];
    }
  }

  for (const line of lines) {
    if (!line.trim()) {
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      flushParagraph();
      continue;
    }
    if (line.match(/^- (.+)$/)) {
      flushParagraph();
      if (!inList) {
        result.push('<ul class="list-none p-0 m-0 mb-4 space-y-1">');
        inList = true;
      }
      const itemContent = line
        .replace(/^- (.+)$/, '$1')
        .replace(
          /\[([^\]]+)\]\(([^)]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>',
        )
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong class="text-ink-200 font-semibold">$1</strong>');
      result.push(
        `<li class="text-sm text-ink-400 pl-4 relative before:content-['▸'] before:absolute before:left-0 before:text-accent-500 before:text-xs before:top-0.5">${itemContent}</li>`,
      );
      continue;
    }
    if (line.startsWith('<') || line.startsWith('%%CODEBLOCK_')) {
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      flushParagraph();
      result.push(line);
      continue;
    }
    if (inList) {
      result.push('</ul>');
      inList = false;
    }
    const processed = line
      .replace(
        /\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>',
      )
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong class="text-ink-200 font-semibold">$1</strong>');
    paragraphBuf.push(processed);
  }
  if (inList) result.push('</ul>');
  flushParagraph();

  let output = result.join('\n');
  output = output.replace(/%%CODEBLOCK_(\d+)%%/g, (_, idx) => codeBlocks[parseInt(idx)] || '');
  return output;
}

// ---- Color map for extra config highlighting ----
const CONFIG_COLORS: Record<string, string> = {
  'mtp-spec-decoding': 'text-amber-400',
  'prefix-caching': 'text-emerald-400',
  'async-scheduling': 'text-sky-400',
  flashcomm1: 'text-rose-400',
  'npugraph-ex': 'text-violet-400',
  'cpu-binding': 'text-cyan-400',
  'dsa-cp': 'text-orange-400',
  'multistream-overlap': 'text-pink-400',
};

// ---- Config placeholder replacer ----
// Replaces %%CONFIG:key%%...%%/CONFIG:key%% blocks based on selected configs.
// If config_values specified, uses enabled/disabled strings; otherwise keeps/removes content.
// After replacement, cleans up blank lines and trailing commas left by removed blocks.
function applyConfigReplace(
  html: string,
  selectedConfigs: Set<string>,
  configValues?: Record<string, { enabled: string; disabled: string }>,
): string {
  let result = html.replace(
    /([ \t]*)%%CONFIG:(\w[\w-]*)%%([\s\S]*?)%%\/CONFIG:\2%%/g,
    (_, indent: string, key: string, content: string) => {
      if (configValues?.[key]) {
        const val = selectedConfigs.has(key)
          ? configValues[key].enabled
          : configValues[key].disabled;
        if (selectedConfigs.has(key)) {
          return `${indent}%%HL:${key}%%${val}%%/HL:${key}%%`;
        }
        return indent + val;
      }
      if (selectedConfigs.has(key)) {
        // Preserve the content, wrap with color highlight markers
        return `${indent}%%HL:${key}%%${content}%%/HL:${key}%%`;
      }
      return '';
    },
  );
  // Clean up: remove blank lines, trailing commas, and consecutive empty lines
  // left by removed config blocks (including inside JSON objects)
  result = result.replace(/,\s*\n/g, '\n'); // remove trailing comma + newline
  result = result.replace(/^\s*\n/gm, ''); // remove blank lines
  result = result.replace(/\n\s*\n\s*\n/g, '\n\n'); // collapse multiple blank lines
  return result;
}

// ---- Color highlight applier ----
// Replaces %%HL:key%%...%%/HL:key%% markers with colored <span> tags.
// Called on the FINAL rendered HTML (after renderMarkdown) so spans don't get escaped.
function applyColorHighlights(html: string, _selectedConfigs: Set<string>): string {
  return html.replace(
    /%%HL:(\w[\w-]*)%%([\s\S]*?)%%\/HL:\1%%/g,
    (_, key: string, content: string) => {
      const colorClass = CONFIG_COLORS[key] || 'text-ink-400';
      return `<span class="config-hl ${colorClass}">${content}</span>`;
    },
  );
}

// ---- Component ----
export default function CascadeSelector({
  scenariosEn,
  scenariosZh,
  extraConfigEn,
  extraConfigZh,
}: CascadeSelectorProps) {
  const { lang, t } = useLang();
  const scenarios = lang === 'zh' ? scenariosZh : scenariosEn;
  const extraConfig =
    lang === 'zh' ? (extraConfigZh ?? extraConfigEn) : (extraConfigEn ?? extraConfigZh);

  const npus = useMemo(() => {
    const set = new Set<string>();
    scenarios.forEach((s) => set.add(s.npu));
    return Array.from(set);
  }, [scenarios]);

  const [selectedNpu, setSelectedNpu] = useState(npus[0] || '');

  const precisions = useMemo(() => {
    const set = new Set<string>();
    scenarios.filter((s) => s.npu === selectedNpu).forEach((s) => set.add(s.precision));
    return Array.from(set);
  }, [scenarios, selectedNpu]);

  const [selectedPrecision, setSelectedPrecision] = useState(precisions[0] || '');

  useEffect(() => {
    if (!precisions.includes(selectedPrecision)) {
      setSelectedPrecision(precisions[0] || '');
    }
  }, [precisions, selectedPrecision]);

  const effectivePrecision = precisions.includes(selectedPrecision)
    ? selectedPrecision
    : precisions[0] || '';

  const deployments = useMemo(() => {
    const set = new Set<string>();
    scenarios
      .filter((s) => s.npu === selectedNpu && s.precision === effectivePrecision)
      .forEach((s) => set.add(s.deployment));
    return Array.from(set);
  }, [scenarios, selectedNpu, effectivePrecision]);

  const [selectedDeployment, setSelectedDeployment] = useState(deployments[0] || '');

  useEffect(() => {
    if (!deployments.includes(selectedDeployment)) {
      setSelectedDeployment(deployments[0] || '');
    }
  }, [deployments, selectedDeployment]);

  const effectiveDeployment = deployments.includes(selectedDeployment)
    ? selectedDeployment
    : deployments[0] || '';

  const cases = useMemo(() => {
    const set = new Set<string>();
    scenarios
      .filter(
        (s) =>
          s.npu === selectedNpu &&
          s.precision === effectivePrecision &&
          s.deployment === effectiveDeployment,
      )
      .forEach((s) => set.add(s.case));
    return Array.from(set);
  }, [scenarios, selectedNpu, effectivePrecision, effectiveDeployment]);

  const [selectedCase, setSelectedCase] = useState(cases[0] || '');

  useEffect(() => {
    if (!cases.includes(selectedCase)) {
      setSelectedCase(cases[0] || '');
    }
  }, [cases, selectedCase]);

  const effectiveCase = cases.includes(selectedCase) ? selectedCase : cases[0] || '';

  const currentScenario = scenarios.find(
    (s) =>
      s.npu === selectedNpu &&
      s.precision === effectivePrecision &&
      s.deployment === effectiveDeployment &&
      s.case === effectiveCase,
  );

  // Step tab state — reset when scenario changes
  const [activeStep, setActiveStep] = useState(0);
  useEffect(() => {
    setActiveStep(0);
  }, [currentScenario]);

  // Extra config multi-select — initialize from current scenario defaults
  const [selectedConfigs, setSelectedConfigs] = useState<Set<string>>(() => {
    if (currentScenario?.default_configs) {
      return new Set(currentScenario.default_configs);
    }
    return new Set();
  });

  useEffect(() => {
    if (currentScenario?.default_configs) {
      setSelectedConfigs(new Set(currentScenario.default_configs));
    } else {
      setSelectedConfigs(new Set());
    }
  }, [currentScenario]);

  const toggleConfig = (key: string) => {
    setSelectedConfigs((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const chipClass = (active: boolean) =>
    `px-3 py-1.5 text-xs font-mono rounded-md transition-all cursor-pointer ${
      active
        ? 'bg-accent-500/10 text-accent-400 border border-accent-500/30'
        : 'border border-ink-700/60 text-ink-400 hover:text-ink-200 hover:border-ink-600 bg-ink-900/50'
    }`;

  // Resolve rendered content for current step (hooks must run before any
  // early return to keep call order stable across renders)
  const currentStep = currentScenario?.steps[activeStep];
  const rawContent = useMemo(() => {
    if (!currentStep) return '';
    return applyConfigReplace(currentStep.content, selectedConfigs, currentStep.config_values);
  }, [currentStep, selectedConfigs]);

  const renderedHtml = useMemo(() => {
    if (!rawContent) return '';
    const mdHtml = renderMarkdown(rawContent);
    return applyColorHighlights(mdHtml, selectedConfigs);
  }, [rawContent, selectedConfigs]);

  if (npus.length === 0) return null;

  // ---- Build filter rows ----
  interface FilterRow {
    label: string;
    options: string[];
    selected: string;
    onSelect: (v: string) => void;
  }

  const rows: FilterRow[] = [
    { label: t('labelNpu'), options: npus, selected: selectedNpu, onSelect: setSelectedNpu },
    {
      label: t('labelPrecision'),
      options: precisions,
      selected: effectivePrecision,
      onSelect: setSelectedPrecision,
    },
    {
      label: t('labelDeployment'),
      options: deployments,
      selected: effectiveDeployment,
      onSelect: setSelectedDeployment,
    },
    { label: t('labelCase'), options: cases, selected: effectiveCase, onSelect: setSelectedCase },
  ];

  return (
    <div>
      {/* Filter panel */}
      <div className="rounded-lg border border-ink-800/60 bg-ink-900/70 overflow-hidden mb-8">
        {rows.map((row, ri) => (
          <div
            key={ri}
            className={`flex items-start gap-4 px-4 py-3 ${
              ri < rows.length - 1 ? 'border-b border-ink-800/40' : ''
            }`}
          >
            <span className="shrink-0 w-24 pt-0.5 text-xs font-mono text-ink-300">{row.label}</span>
            <div className="flex flex-wrap gap-1.5">
              {row.options.map((opt) => (
                <button
                  key={opt}
                  onClick={() => row.onSelect(opt)}
                  className={chipClass(row.selected === opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>
        ))}

        {/* More Config — multi-select, synced with scenario defaults */}
        {extraConfig && extraConfig.length > 0 && (
          <div className="flex items-start gap-4 px-4 py-3 border-t border-ink-800/40">
            <span className="shrink-0 w-24 pt-0.5 text-xs font-mono text-ink-300">
              {t('labelMoreConfig')}
            </span>
            <div className="flex flex-wrap gap-1.5">
              {extraConfig.map((cfg) => {
                const isSelected = selectedConfigs.has(cfg.key);
                const colorClass = CONFIG_COLORS[cfg.key] || 'text-ink-400';
                const bgClass = colorClass.replace('text-', 'bg-');
                return (
                  <button
                    key={cfg.key}
                    onClick={() => toggleConfig(cfg.key)}
                    title={cfg.label}
                    className={`px-3 py-1.5 text-xs font-mono rounded-md transition-all cursor-pointer inline-flex items-center gap-1.5 ${
                      isSelected
                        ? `${colorClass} bg-accent-500/10 border border-current/30`
                        : 'border border-ink-700/60 text-ink-400 hover:text-ink-200 hover:border-ink-600 bg-ink-900/50'
                    }`}
                  >
                    <span
                      className={`inline-block w-2 h-2 rounded-full ${bgClass} ${isSelected ? 'opacity-100' : 'opacity-30'}`}
                    ></span>
                    {cfg.label}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Result panel */}
      {currentScenario && (
        <div className="rounded-lg border border-ink-800/60 overflow-hidden">
          {/* Step tabs header */}
          <div className="flex items-center border-b border-ink-800/60 bg-ink-900/70">
            <span className="shrink-0 px-3 text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider">
              {t('step') || 'Steps'}
            </span>
            {currentScenario.steps.map((step, i) => (
              <button
                key={i}
                onClick={() => setActiveStep(i)}
                className={`px-4 py-2.5 text-xs font-mono font-semibold transition-all border-b-2 -mb-px ${
                  activeStep === i
                    ? 'border-accent-400 text-accent-400 bg-accent-500/5'
                    : 'border-transparent text-ink-500 hover:text-ink-300 hover:bg-ink-800/40'
                }`}
              >
                {i + 1}
              </button>
            ))}
          </div>

          {/* Active step content */}
          {currentStep && (
            <div className="px-5 py-5 bg-ink-950/60">
              <div className="flex items-center gap-3 mb-4">
                <span className="inline-flex items-center justify-center w-7 h-7 rounded-md border border-accent-500/20 bg-accent-500/5 font-mono text-xs text-accent-400 font-semibold">
                  {activeStep + 1}
                </span>
                <h3 className="font-display text-lg font-semibold text-ink-50">
                  {currentStep.title}
                </h3>
              </div>
              <div className="ml-[22px] prose" dangerouslySetInnerHTML={{ __html: renderedHtml }} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
