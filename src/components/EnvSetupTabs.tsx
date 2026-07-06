import { useState, useMemo } from 'react';
import { useLang } from '../lib/useLang';

interface EnvSetupItem { content: string; }
interface ContainerEnv { [npu: string]: EnvSetupItem; }
interface EnvSetup { pip?: EnvSetupItem; container?: ContainerEnv; }
interface EnvSetupTabsProps { envSetupEn: EnvSetup; envSetupZh: EnvSetup; }

function renderMarkdown(md: string): string {
  let html = md;

  // Extract code blocks with placeholders to prevent split() from breaking them
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

  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-accent-500/40 pl-4 py-2 my-4 bg-accent-500/5 rounded-r text-sm text-ink-400">$1</blockquote>');

  const lines = html.split('\n');
  const result: string[] = [];
  let paragraphBuf: string[] = [];

  function flushParagraph() {
    if (paragraphBuf.length > 0) {
      result.push(`<p class="text-sm text-ink-400 leading-relaxed mb-4">${paragraphBuf.join('<br />\n')}</p>`);
      paragraphBuf = [];
    }
  }

  for (const line of lines) {
    if (!line.trim()) {
      flushParagraph();
      continue;
    }
    if (line.startsWith('<') || line.startsWith('%')) {
      flushParagraph();
      result.push(line);
      continue;
    }
    const processed = line
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
    paragraphBuf.push(processed);
  }
  flushParagraph();

  let output = result.join('\n');
  output = output.replace(/%%CODEBLOCK_(\d+)%%/g, (_, idx) => codeBlocks[parseInt(idx)] || '');
  return output;
}

export default function EnvSetupTabs({ envSetupEn, envSetupZh }: EnvSetupTabsProps) {
  const { lang, t } = useLang();
  const envSetup = lang === 'zh' ? envSetupZh : envSetupEn;
  const hasPip = !!envSetup.pip;
  const hasContainer = !!envSetup.container && Object.keys(envSetup.container).length > 0;

  const [mainTab, setMainTab] = useState<'pip' | 'container'>(hasPip ? 'pip' : 'container');

  const containerNpus = useMemo(() => {
    if (!envSetup.container) return [];
    return Object.keys(envSetup.container).sort();
  }, [envSetup.container]);

  const [containerTab, setContainerTab] = useState(containerNpus[0] || '');

  const tabClass = (active: boolean) =>
    `px-4 py-2 text-xs font-mono rounded-md transition-all ${
      active
        ? 'bg-accent-500/10 text-accent-400 border border-accent-500/30'
        : 'border border-ink-800/60 text-ink-500 hover:text-ink-300 hover:border-ink-700'
    }`;

  const showMainTabs = hasPip && hasContainer;

  const currentContent = mainTab === 'pip'
    ? envSetup.pip?.content
    : (envSetup.container?.[containerTab]?.content ?? '');

  return (
    <div>
      {showMainTabs && (
        <div className="flex gap-2 mb-4">
          <button onClick={() => setMainTab('pip')} className={tabClass(mainTab === 'pip')}>
            {t('tabPip')}
          </button>
          <button onClick={() => setMainTab('container')} className={tabClass(mainTab === 'container')}>
            {t('tabContainer')}
          </button>
        </div>
      )}

      {mainTab === 'container' && containerNpus.length > 1 && (
        <div className="flex gap-2 mb-4">
          {containerNpus.map((npu) => (
            <button key={npu} onClick={() => setContainerTab(npu)} className={tabClass(containerTab === npu)}>
              {npu}
            </button>
          ))}
        </div>
      )}

      {currentContent && (
        <div className="prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(currentContent) }} />
      )}
    </div>
  );
}
