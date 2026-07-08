import { useState } from 'react';
import { useLang } from '../lib/useLang';

interface PrerequisiteItem {
  title: string;
  content: string;
}

interface EnvSetupItem {
  content: string;
}

interface ContainerEnv {
  [npu: string]: EnvSetupItem;
}

interface EnvSetup {
  pip?: EnvSetupItem;
  container?: ContainerEnv;
}

interface PrepareTabsProps {
  overviewEn: string;
  overviewZh: string;
  prerequisitesEn?: PrerequisiteItem[];
  prerequisitesZh?: PrerequisiteItem[];
  envSetupEn: EnvSetup;
  envSetupZh: EnvSetup;
  hasQuantization: boolean;
  quantizationEn?: string;
  quantizationZh?: string;
}

function renderContent(md: string): string {
  if (!md) return '';

  let html = md;

  // Extract code blocks with placeholders
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

  // Table
  html = html.replace(/(?:^\|.+\|$\n?)+/gm, (match) => {
    const rows = match.trim().split('\n');
    let tableHtml = '<div class="overflow-x-auto rounded-lg border border-ink-800/60 mb-4"><table class="w-full text-sm">';
    let headerDone = false;
    for (const row of rows) {
      const cells = row.split('|').filter(c => c.trim() !== '');
      if (cells.every(c => /^:?-{3,}:?$/.test(c.trim()))) { headerDone = true; continue; }
      const cellHtml = cells.map((c, i) => {
        const tag = !headerDone && i === 0 ? 'th' : 'td';
        const cls = tag === 'th'
          ? 'text-left py-2.5 px-4 font-mono text-xs font-medium text-ink-300 uppercase tracking-wider'
          : 'py-2.5 px-4 text-ink-400 font-mono text-xs';
        return `<${tag} class="${cls}">${c.trim()}</${tag}>`;
      }).join('');
      if (!headerDone) { tableHtml += `<thead><tr class="border-b border-ink-800/60 bg-ink-900/40">${cellHtml}</tr></thead>`; headerDone = true; }
      else { tableHtml += `<tbody><tr class="border-b border-ink-800/40 last:border-0 hover:bg-ink-900/30 transition-colors">${cellHtml}</tr></tbody>`; }
    }
    tableHtml += '</table></div>';
    return tableHtml;
  });

  html = html.replace(/^#### (.+)$/gm, '<h4 class="font-display text-sm font-semibold mt-4 mb-2 text-ink-300">$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3 class="font-display text-base font-semibold mt-6 mb-2 text-ink-200">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="font-display text-lg font-semibold mt-6 mb-3 text-ink-100">$1</h2>');
  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-accent-500/40 pl-4 py-2 my-4 bg-accent-500/5 rounded-r text-sm text-ink-400">$1</blockquote>');

  const lines = html.split('\n');
  const result: string[] = [];
  let inList = false;
  let paragraphBuf: string[] = [];

  function flushParagraph() {
    if (paragraphBuf.length > 0) {
      result.push(`<p class="text-sm text-ink-400 leading-relaxed mb-4">${paragraphBuf.join('<br />\n')}</p>`);
      paragraphBuf = [];
    }
  }

  for (const line of lines) {
    if (!line.trim()) {
      if (inList) { result.push('</ul>'); inList = false; }
      flushParagraph();
      continue;
    }
    if (line.match(/^- (.+)$/)) {
      flushParagraph();
      if (!inList) { result.push('<ul class="list-none p-0 m-0 mb-4 space-y-1">'); inList = true; }
      const itemContent = line.replace(/^- (.+)$/, '$1')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong class="text-ink-200 font-semibold">$1</strong>');
      result.push(`<li class="text-sm text-ink-400 pl-4 relative before:content-['▸'] before:absolute before:left-0 before:text-accent-500 before:text-xs before:top-0.5">${itemContent}</li>`);
      continue;
    }
    if (line.startsWith('<') || line.startsWith('%%CODEBLOCK_')) {
      if (inList) { result.push('</ul>'); inList = false; }
      flushParagraph();
      result.push(line);
      continue;
    }
    if (inList) { result.push('</ul>'); inList = false; }
    const processed = line
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>')
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

type TabId = 'overview' | 'prerequisites' | 'env-setup';

export default function PrepareTabs({
  overviewEn, overviewZh,
  prerequisitesEn, prerequisitesZh,
  envSetupEn, envSetupZh,
  hasQuantization, quantizationEn, quantizationZh,
}: PrepareTabsProps) {
  const { lang, t } = useLang();
  const overview = lang === 'zh' ? overviewZh : overviewEn;
  const prerequisites = lang === 'zh' && prerequisitesZh ? prerequisitesZh : (prerequisitesEn || []);
  const envSetup = lang === 'zh' ? envSetupZh : envSetupEn;
  const quantization = lang === 'zh' && quantizationZh ? quantizationZh : (quantizationEn || '');

  const hasPrerequisites = !!prerequisitesEn && prerequisitesEn.length > 0;
  const hasEnvSetup = !!(envSetup.pip || (envSetup.container && Object.keys(envSetup.container).length > 0));

  // Build available tabs
  const tabs: { id: TabId; label: string }[] = [{ id: 'overview', label: t('sectionOverview') }];
  if (hasPrerequisites) tabs.push({ id: 'prerequisites', label: t('sectionPrerequisites') });
  if (hasEnvSetup) tabs.push({ id: 'env-setup', label: t('sectionEnvSetup') });

  const [active, setActive] = useState<TabId>(tabs[0].id);

  const tabClass = (id: TabId) =>
    `px-4 py-2.5 text-xs font-mono transition-all border-b-2 -mb-px ${
      active === id
        ? 'border-accent-400 text-accent-400 bg-accent-500/5'
        : 'border-transparent text-ink-400 hover:text-ink-200 hover:bg-ink-800/40'
    }`;

  // Env setup sub-tabs
  const envHasPip = !!envSetup.pip;
  const envHasContainer = !!envSetup.container && Object.keys(envSetup.container).length > 0;
  const [envMain, setEnvMain] = useState<'pip' | 'container'>(envHasPip ? 'pip' : 'container');
  const containerNpus = envSetup.container ? Object.keys(envSetup.container).sort() : [];
  const [containerTab, setContainerTab] = useState(containerNpus[0] || '');

  const envSubTabClass = (active: boolean) =>
    `px-3 py-1.5 text-xs font-mono rounded-md transition-all cursor-pointer ${
      active
        ? 'bg-accent-500/10 text-accent-400 border border-accent-500/30'
        : 'border border-ink-700/60 text-ink-400 hover:text-ink-200 hover:border-ink-600 bg-ink-900/50'
    }`;

  const envContent = envMain === 'pip'
    ? envSetup.pip?.content
    : (envSetup.container?.[containerTab]?.content ?? '');

  return (
    <div className="rounded-lg border border-ink-800/60 overflow-hidden">
      {/* Tabs header */}
      <div className="flex border-b border-ink-800/60 bg-ink-900/70">
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => setActive(tab.id)} className={tabClass(tab.id)}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="px-5 py-5 bg-ink-950/60">
        {active === 'overview' && (
          <div className="prose" dangerouslySetInnerHTML={{ __html: renderContent(overview) }} />
        )}

        {active === 'prerequisites' && (
          <div className="space-y-6">
            {prerequisites.map((item, i) => (
              <div key={i}>
                <h3 className="font-display text-base font-semibold text-ink-200 mb-2">{item.title}</h3>
                <div className="prose" dangerouslySetInnerHTML={{ __html: renderContent(item.content) }} />
              </div>
            ))}
          </div>
        )}

        {active === 'env-setup' && (
          <div>
            {envHasPip && envHasContainer && (
              <div className="flex gap-2 mb-4">
                <button onClick={() => setEnvMain('pip')} className={envSubTabClass(envMain === 'pip')}>
                  {t('tabPip')}
                </button>
                <button onClick={() => setEnvMain('container')} className={envSubTabClass(envMain === 'container')}>
                  {t('tabContainer')}
                </button>
              </div>
            )}

            {envMain === 'container' && containerNpus.length > 1 && (
              <div className="flex flex-wrap gap-2 mb-4">
                {containerNpus.map((npu) => (
                  <button key={npu} onClick={() => setContainerTab(npu)} className={envSubTabClass(containerTab === npu)}>
                    {npu}
                  </button>
                ))}
              </div>
            )}

            {envContent && (
              <div className="prose" dangerouslySetInnerHTML={{ __html: renderContent(envContent) }} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
