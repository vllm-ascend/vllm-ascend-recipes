import { useState } from 'react';
import { useLang } from '../lib/useLang';

interface WeightSource {
  source: string;
  url: string;
  command: string;
}

interface WeightDownload {
  weight_version: string;
  sources: WeightSource[];
}

interface WeightDownloadTabsProps {
  downloadsEn: WeightDownload[];
  downloadsZh: WeightDownload[];
}

function renderMarkdown(md: string): string {
  let html = md;
  const codeBlocks: string[] = [];

  // Replace code blocks with placeholders to protect their content from further processing
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const escaped = code
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .trimEnd();
    const block = `<div class="code-block group relative"><div class="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity z-10"><button class="copy-btn px-2 py-1 text-[10px] font-mono rounded border border-ink-700 bg-ink-800 text-ink-400 hover:text-accent-400 hover:border-accent-500/30 transition-colors" data-code="${encodeURIComponent(code.trim())}">copy</button></div><pre><code class="language-${lang || 'bash'}">${escaped}</code></pre></div>`;
    codeBlocks.push(block);
    return `%%CODEBLOCK_${codeBlocks.length - 1}%%`;
  });

  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-accent-500/40 pl-4 py-2 my-4 bg-accent-500/5 rounded-r text-sm text-ink-400">$1</blockquote>');

  const lines = html.split('\n');
  const result: string[] = [];

  for (const line of lines) {
    if (line.trim() && !line.startsWith('<') && !line.startsWith('```') && !line.startsWith('%%CODEBLOCK_')) {
      const processed = line
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">$1</a>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
      result.push(`<p class="text-sm text-ink-400 leading-relaxed mb-4">${processed}</p>`);
    } else {
      result.push(line);
    }
  }

  // Restore code blocks from placeholders
  return result.join('\n').replace(/%%CODEBLOCK_(\d+)%%/g, (_, idx) => codeBlocks[parseInt(idx)]);
}

export default function WeightDownloadTabs({ downloadsEn, downloadsZh }: WeightDownloadTabsProps) {
  const { lang, t } = useLang();
  const downloads = lang === 'zh' ? downloadsZh : downloadsEn;
  const [versionIdx, setVersionIdx] = useState(0);
  const [sourceIdx, setSourceIdx] = useState(0);

  const currentDownload = downloads[versionIdx] || downloads[0];
  if (!currentDownload) return null;

  const currentSource = currentDownload.sources[sourceIdx] || currentDownload.sources[0];
  if (!currentSource) return null;

  const effectiveSourceIdx = currentDownload.sources.findIndex(
    (s) => s.source === currentSource.source
  );

  const tabClass = (active: boolean) =>
    `px-3 py-1.5 text-xs font-mono rounded-md transition-all ${
      active
        ? 'bg-accent-500/10 text-accent-400 border border-accent-500/30'
        : 'border border-ink-800/60 text-ink-500 hover:text-ink-300 hover:border-ink-700'
    }`;

  return (
    <div>
      {downloads.length > 1 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {downloads.map((d, i) => (
            <button
              key={i}
              onClick={() => { setVersionIdx(i); setSourceIdx(0); }}
              className={tabClass(versionIdx === i)}
            >
              {d.weight_version}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-4">
        {currentDownload.sources.map((s, i) => (
          <button
            key={i}
            onClick={() => setSourceIdx(i)}
            className={tabClass(i === effectiveSourceIdx)}
          >
            {s.source}
          </button>
        ))}
      </div>

      <div className="prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(currentSource.command) }} />

      {currentSource.url && (
        <a
          href={currentSource.url}
          target="_blank"
          rel="noopener"
          className="inline-flex items-center gap-1.5 mt-3 text-xs font-mono text-accent-400 hover:text-accent-300 transition-colors"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3" />
          </svg>
          {t('goSource')} {currentSource.source}
        </a>
      )}
    </div>
  );
}
