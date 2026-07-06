import { useState, useMemo, useEffect } from 'react';
import { useLang } from '../lib/useLang';

interface ScenarioStep {
  title: string;
  content: string;
}

interface Scenario {
  npu: string;
  precision: string;
  deployment: string;
  verified: boolean;
  steps: ScenarioStep[];
}

interface CascadeSelectorProps {
  scenariosEn: Scenario[];
  scenariosZh: Scenario[];
}

function renderMarkdown(md: string): string {
  let html = md;

  // Extract code blocks, replace with placeholders to prevent split() from
  // breaking their internal newlines.
  const codeBlocks: string[] = [];
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const escaped = code
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .trimEnd();
    const idx = codeBlocks.length;
    codeBlocks.push(`<div class="code-block group relative"><div class="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity z-10"><button class="copy-btn px-2 py-1 text-[10px] font-mono rounded border border-ink-700 bg-ink-800 text-ink-400 hover:text-accent-400 hover:border-accent-500/30 transition-colors" data-code="${encodeURIComponent(code.trim())}">copy</button></div><pre><code class="language-${lang || 'bash'}">${escaped}</code></pre></div>`);
    return `%%CODEBLOCK_${idx}%%`;
  });

  // Table support
  html = html.replace(/(?:^\|.+\|$\n?)+/gm, (match) => {
    const rows = match.trim().split('\n');
    let tableHtml = '<div class="overflow-x-auto rounded-lg border border-ink-800/60 mb-4"><table class="w-full text-sm">';
    let headerDone = false;
    for (const row of rows) {
      const cells = row.split('|').filter(c => c.trim() !== '');
      if (cells.every(c => /^:?-{3,}:?$/.test(c.trim()))) {
        headerDone = true;
        continue;
      }
      const cellHtml = cells.map((c, i) => {
        const tag = !headerDone && i === 0 ? 'th' : 'td';
        const cls = tag === 'th'
          ? 'text-left py-2.5 px-4 font-mono text-xs font-medium text-ink-300 uppercase tracking-wider'
          : 'py-2.5 px-4 text-ink-400 font-mono text-xs';
        return `<${tag} class="${cls}">${c.trim()}</${tag}>`;
      }).join('');
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

  html = html.replace(/^### (.+)$/gm, '<h3 class="font-display text-base font-semibold mt-6 mb-2 text-ink-200">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="font-display text-lg font-semibold mt-6 mb-3 text-ink-100">$1</h2>');
  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-accent-500/40 pl-4 py-2 my-4 bg-accent-500/5 rounded-r text-sm text-ink-400">$1</blockquote>');

  const lines = html.split('\n');
  const result: string[] = [];
  let inList = false;
  let paragraphBuf: string[] = [];

  function flushParagraph() {
    if (paragraphBuf.length > 0) {
      const content = paragraphBuf.join('<br />\n');
      result.push(`<p class="text-sm text-ink-400 leading-relaxed mb-4">${content}</p>`);
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
      const itemContent = line.replace(/^- (.+)$/, '$1')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
      result.push(`<li class="text-sm text-ink-400 pl-4 relative before:content-['▸'] before:absolute before:left-0 before:text-accent-500 before:text-xs before:top-0.5">${itemContent}</li>`);
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
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong class="text-ink-200 font-semibold">$1</strong>');
    paragraphBuf.push(processed);
  }

  if (inList) result.push('</ul>');
  flushParagraph();

  // Restore code blocks
  let output = result.join('\n');
  output = output.replace(/%%CODEBLOCK_(\d+)%%/g, (_, idx) => codeBlocks[parseInt(idx)] || '');

  return output;
}

export default function CascadeSelector({ scenariosEn, scenariosZh }: CascadeSelectorProps) {
  const { lang, t } = useLang();
  const scenarios = lang === 'zh' ? scenariosZh : scenariosEn;

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

  const currentScenario = scenarios.find(
    (s) => s.npu === selectedNpu && s.precision === effectivePrecision && s.deployment === effectiveDeployment
  );

  const selectClass = "w-full px-3 py-2 text-xs font-mono rounded-lg border border-ink-800/60 bg-ink-900/40 text-ink-200 focus:outline-none focus:border-accent-500/40 focus:ring-1 focus:ring-accent-500/20 cursor-pointer transition-colors";
  const labelClass = "text-[10px] font-mono uppercase tracking-wider text-ink-600 mb-1.5 block";

  return (
    <div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8 p-4 rounded-lg border border-ink-800/60 bg-ink-900/30">
        <div>
          <label className={labelClass}>{t('labelNpu')}</label>
          <select value={selectedNpu} onChange={(e) => setSelectedNpu(e.target.value)} className={selectClass}>
            {npus.map((n) => (<option key={n} value={n}>{n}</option>))}
          </select>
        </div>
        <div>
          <label className={labelClass}>{t('labelPrecision')}</label>
          <select value={effectivePrecision} onChange={(e) => setSelectedPrecision(e.target.value)} className={selectClass}>
            {precisions.map((p) => (<option key={p} value={p}>{p}</option>))}
          </select>
        </div>
        <div>
          <label className={labelClass}>{t('labelDeployment')}</label>
          <select value={effectiveDeployment} onChange={(e) => setSelectedDeployment(e.target.value)} className={selectClass}>
            {deployments.map((d) => (<option key={d} value={d}>{d}</option>))}
          </select>
        </div>
      </div>

      {currentScenario && (
        <div>
          <div class="space-y-8">
            {currentScenario.steps.map((step, i) => (
              <div key={i} className="relative">
                <div className="flex items-center gap-3 mb-3">
                  <span className="inline-flex items-center justify-center w-6 h-6 rounded-md border border-accent-500/20 bg-accent-500/5 font-mono text-xs text-accent-400 font-semibold">
                    {i + 1}
                  </span>
                  <h3 className="font-display text-base font-semibold text-ink-100">{step.title}</h3>
                </div>
                <div className="ml-[18px] prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(step.content) }} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
