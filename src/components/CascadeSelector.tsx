import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useLang } from '../lib/useLang';
import { resolveVllmAscendLink } from '../lib/links';
import { expandScenarioScripts } from '../lib/scenario-scripts';
import type { ScenarioScript, Variant } from '../lib/types';
import {
  roleNodeCount,
  loadPdEndpoints,
  savePdEndpoint,
  substitutePdContent,
} from '../lib/pd-cluster';

// Map the UI's NPU display name to the strategy_overrides hardware key.
function hwKeyForNpu(npu: string): string {
  const n = npu.toLowerCase();
  if (n.includes('300i') || n.includes('duo')) return 'atlas_300i_duo';
  if (n.includes('a2')) return 'atlas_800_a2';
  if (n.includes('a3')) return 'atlas_800_a3';
  return 'default';
}

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
  tags?: string[];
  strategy?: string;
  scripts?: Record<string, ScenarioScript>;
  steps: ScenarioStep[];
  default_configs?: string[];
  config_params?: Record<string, ConfigParam>;
}

interface ConfigParam {
  default?: unknown;
  type?: 'number' | 'string' | 'bool';
  description?: string;
  flag?: string;
  flag_when_false?: string;
}

interface FeatureMeta {
  label?: string;
  description?: string;
  args?: string[];
  env?: Record<string, string>;
  flag_when_false?: string;
}

interface CascadeSelectorProps {
  scenariosEn: Scenario[];
  scenariosZh: Scenario[];
  extraConfigEn?: ExtraConfigItem[];
  extraConfigZh?: ExtraConfigItem[];
  configParamsEn?: Record<string, ConfigParam>;
  configParamsZh?: Record<string, ConfigParam>;
  featuresEn?: Record<string, FeatureMeta>;
  featuresZh?: Record<string, FeatureMeta>;
  optInFeaturesEn?: string[];
  optInFeaturesZh?: string[];
  variantsEn?: Record<string, Variant>;
  variantsZh?: Record<string, Variant>;
  hardwareStatus?: Record<string, string>;
  selectorLabelsEn?: Partial<Record<'npu' | 'precision' | 'deployment' | 'case', string>>;
  selectorLabelsZh?: Partial<Record<'npu' | 'precision' | 'deployment' | 'case', string>>;
  pdCluster?: {
    env?: Record<string, string>;
    prefill?: { nodes?: number | { default?: number } };
    decode?: { nodes?: number | { default?: number } };
  };
}

// Legacy pipeline-routing tags remain visible while the new template-driven
// multi-node flow uses test_id/scripts independently.
const PIPELINE_LABELS: Record<string, Record<string, string>> = {
  zh: {
    'a2-single': 'A2 单机流水线',
    'a3-single': 'A3 单机流水线',
    'pd-multinode': '多机 PD 流水线',
  },
  en: {
    'a2-single': 'A2 Single-Node Pipeline',
    'a3-single': 'A3 Single-Node Pipeline',
    'pd-multinode': 'Multi-Node PD Pipeline',
  },
};

// Quote an argv token for the shell line (JSON/spacey args from features).
function renderArg(a: string): string {
  return /[\s{}]/.test(a) && !/^['"]/.test(a) ? `'${a}'` : a;
}

// Render an upstream-style feature toggle to shell text.
function renderFeature(f: FeatureMeta, on: boolean): string {
  if (!on) return f.flag_when_false ?? '';
  const parts: string[] = [];
  if (f.args && f.args.length) parts.push(f.args.map(renderArg).join(' '));
  if (f.env) {
    for (const [k, v] of Object.entries(f.env)) parts.push(`export ${k}=${v}`);
  }
  return parts.join('\n');
}

// ---- Hover tooltip (portal-based so the filter panel's overflow-hidden
// border never clips it; mirrors the vllm recipes command-builder) ----
function Tooltip({ content, children }: { content?: string; children: React.ReactNode }) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  const show = useCallback(() => {
    if (!content || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const width = 320; // w-80
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    setPos({ top: r.bottom + 8, left });
  }, [content]);

  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [pos]);

  if (!content) return <>{children}</>;
  return (
    <span
      ref={ref}
      className="relative inline-flex"
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
      onFocus={show}
      onBlur={() => setPos(null)}
    >
      {children}
      {pos &&
        createPortal(
          <div
            role="tooltip"
            style={{ top: pos.top, left: pos.left }}
            className="fixed z-50 w-80 max-w-[85vw] rounded-lg border border-ink-700 bg-ink-900/95 px-3.5 py-2.5 text-xs leading-relaxed text-ink-300 shadow-xl shadow-black/40 pointer-events-none whitespace-pre-line"
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  );
}

// Small ⓘ next to a row label explaining what the row means (vllm ConfigRow hint).
function RowHint({ text }: { text: string }) {
  return (
    <Tooltip content={text}>
      <span
        aria-label="info"
        className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-ink-600 text-[9px] font-mono text-ink-500 hover:text-ink-300 hover:border-ink-500 cursor-help"
      >
        i
      </span>
    </Tooltip>
  );
}

// Verified / experimental status dot for NPU pills (vllm HwStatusDot).
function StatusDot({ status }: { status?: string }) {
  const cls =
    status === 'verified'
      ? 'bg-emerald-400'
      : status === 'experimental'
        ? 'bg-amber-400'
        : 'bg-ink-600';
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${cls} mr-1.5 shrink-0`} aria-hidden />
  );
}

// ---- Rich option metadata (per-language), mirroring the vllm recipes page ----
const NPU_INFO: Record<string, Record<string, string>> = {
  en: {
    a3: 'Huawei Atlas 800I A3 inference server — 8 NPUs in a dual-die design (16 compute chips), 64G or 128G memory per chip. Fastest single-node serving path for Ascend.',
    a2: 'Huawei Atlas 800I A2 (Ascend 910B) server — 8 NPUs × 64G memory; the workhorse node for single- and multi-node serving.',
    duo: 'Atlas 300I Duo inference card (310P) — 96G LPDDR4X per card for budget inference; uses the -310p container image and a conservative context length.',
  },
  zh: {
    a3: '华为 Atlas 800I A3 推理服务器 —— 8 颗 NPU、双芯设计共 16 个计算芯片，每芯 64G/128G 显存，是昇腾上最快的单机推理机型。',
    a2: '华为 Atlas 800I A2（昇腾 910B）服务器 —— 8 颗 NPU × 64G 显存，单机/多机部署的主力机型。',
    duo: 'Atlas 300I Duo 推理卡（310P）—— 每卡 96G LPDDR4X 显存，面向高性价比推理；使用 -310p 容器镜像并采用保守的上下文长度。',
  },
};

function npuInfo(npu: string, lang: string): string | undefined {
  const n = npu.toLowerCase();
  const key =
    n.includes('300i') || n.includes('duo')
      ? 'duo'
      : n.includes('a3')
        ? 'a3'
        : n.includes('a2')
          ? 'a2'
          : '';
  return key ? NPU_INFO[lang]?.[key] : undefined;
}

const LEGACY_DEPLOYMENT_INFO: Array<{ match: string[]; en: string; zh: string }> = [
  {
    match: ['hybrid', '混合'],
    en: 'Prefill-Decode hybrid deployment — prefill and decode run on the same node, which is the simplest path for production-grade throughput on one machine.',
    zh: 'PD（Prefill-Decode）混合部署 —— Prefill 与 Decode 运行在同一节点，适合单机上的面向生产级吞吐。',
  },
  {
    match: ['separation', 'disaggregation', '分离'],
    en: 'Prefill-Decode disaggregation — prefill and decode run on separate node pools connected by KV cache transfer, so each phase scales independently for production-grade throughput.',
    zh: 'PD（Prefill-Decode）分离部署 —— Prefill 与 Decode 运行在不同节点池，通过 KV cache 传输衔接，两个阶段可独立扩缩容，面向生产级吞吐。',
  },
  {
    match: ['multi', '多机', '多节点'],
    en: "The model is sharded across multiple nodes via tensor/expert parallelism — used when weights or KV cache exceed a single node's NPU memory.",
    zh: '模型通过张量/专家并行切分到多个节点 —— 当权重或 KV cache 超过单节点显存时使用。',
  },
  {
    match: ['single', '单机'],
    en: 'Prefill and Decode run on the same node — simplest topology, suited to development, testing, and small-to-medium scale serving.',
    zh: 'Prefill 与 Decode 在同一节点完成 —— 最简单的部署拓扑，适合开发、测试和中小规模推理服务。',
  },
];

function deploymentInfo(deployment: string, lang: string): string | undefined {
  if (deployment === 'pd') {
    return lang === 'zh'
      ? 'Prefill 与 Decode 运行在独立节点池，通过 KV cache 传输衔接。'
      : 'Prefill and Decode run in separate node pools connected by KV cache transfer.';
  }
  if (deployment === 'non-pd') {
    return lang === 'zh'
      ? '非 PD 分离部署，模型可通过并行策略跨节点运行。'
      : 'Non-PD-disaggregated deployment; the model may span nodes through parallelism.';
  }
  const normalized = deployment.toLowerCase();
  for (const info of LEGACY_DEPLOYMENT_INFO) {
    if (info.match.some((term) => normalized.includes(term))) {
      return lang === 'zh' ? info.zh : info.en;
    }
  }
  return undefined;
}

function deploymentLabel(deployment: string, lang: string): string {
  if (deployment === 'pd') return lang === 'zh' ? 'PD 分离' : 'PD Disaggregation';
  if (deployment === 'non-pd') return lang === 'zh' ? '非 PD 分离' : 'Non-PD';
  return deployment;
}

// Pick the yaml variant describing a given precision pill (exact key match
// wins, e.g. `bf16`; otherwise the first variant with a matching precision).
function variantForPrecision(
  variants: Record<string, Variant> | undefined,
  precision: string,
): Variant | undefined {
  if (!variants) return undefined;
  const lower = precision.toLowerCase();
  let fallback: Variant | undefined;
  for (const [key, v] of Object.entries(variants)) {
    if (key.toLowerCase() === lower) return v;
    if (
      fallback === undefined &&
      typeof v.precision === 'string' &&
      v.precision.toLowerCase() === lower
    ) {
      fallback = v;
    }
  }
  return fallback;
}

// Hover text for a precision pill — same shape as the vllm recipes Variant row.
function variantTooltip(v: Variant | undefined, lang: string): string | undefined {
  if (!v) return undefined;
  const parts: string[] = [];
  if (v.description) parts.push(String(v.description));
  if (typeof v.vram_minimum_gb === 'number') {
    parts.push(
      lang === 'zh'
        ? `加载模型权重最少需要 ${v.vram_minimum_gb} GB 显存 —— 对外服务还需额外的 KV cache 显存；显存不足时可通过多节点部署扩展。`
        : `Min ${v.vram_minimum_gb} GB to load — add KV cache for serving. Scale out via multi-node if needed.`,
    );
  }
  return parts.length ? parts.join('\n\n') : undefined;
}

// ---- Feature append synthesis ----
// A feature's `--flag` tokens (env keys) identify whether a step's baseline
// command already includes it.
function featureArgTokens(f: FeatureMeta): string[] {
  return (f.args ?? []).filter((a) => a.startsWith('--'));
}
function featureEnvTokens(f: FeatureMeta): string[] {
  return Object.keys(f.env ?? {});
}

// How a step's content relates to a feature:
//   'placeholder' — the step has a {{key}} / %%CONFIG:key%% marker (toggle
//                   substitution owns rendering; never append)
//   'present'     — every arg/env token is already hardcoded in the content
//   'absent'      — not referenced and not present
function featureHandledIn(
  content: string,
  key: string,
  f: FeatureMeta,
): 'placeholder' | 'present' | 'absent' {
  if (content.includes(`{{${key}}}`) || content.includes(`%%CONFIG:${key}%%`)) return 'placeholder';
  const args = featureArgTokens(f);
  const envs = featureEnvTokens(f);
  const argsPresent = args.length === 0 || args.every((t) => content.includes(t));
  const envPresent =
    envs.length === 0 || envs.every((k) => new RegExp(`^\\s*export ${k}=`, 'm').test(content));
  return argsPresent && envPresent ? 'present' : 'absent';
}

// Append `argText` as a new continuation line at the end of the `vllm serve`
// command inside a fenced code block body (adds the trailing `\` when the
// command currently ends without one).
function appendToServeCommand(code: string, argText: string): string {
  const lines = code.split('\n');
  const start = lines.findIndex((l) => /^\s*vllm serve\b/.test(l));
  if (start === -1) return code;
  let end = start;
  while (end < lines.length - 1 && /\\\s*$/.test(lines[end])) end++;
  if (/\\\s*$/.test(lines[end])) {
    lines.splice(end + 1, 0, '    ' + argText);
  } else {
    lines[end] = lines[end].replace(/\s+$/, '') + ' \\';
    lines.splice(end + 1, 0, '    ' + argText);
  }
  return lines.join('\n');
}

// For every to-be-appended feature, add its args to each `vllm serve` command
// in the rendered markdown. Skips blocks that already contain the flags.
function appendFeatureArgs(content: string, feats: Array<[string, FeatureMeta]>): string {
  if (!feats.length) return content;
  return content.replace(/```(\w+)?\n([\s\S]*?)```/g, (m, lang: string, code: string) => {
    if (!/^\s*vllm serve\b/m.test(code)) return m;
    let out = code;
    for (const [key, f] of feats) {
      const argText = (f.args ?? []).map(renderArg).join(' ');
      const envLines = Object.entries(f.env ?? {}).map(([k, v]) => `export ${k}=${v}`);
      const tokens = featureArgTokens(f);
      const envs = featureEnvTokens(f);
      // Never duplicate a flag the baseline already carries.
      if (tokens.length && tokens.every((t) => out.includes(t))) continue;
      if (envs.length && envs.every((k) => new RegExp(`^\\s*export ${k}=`, 'm').test(out)))
        continue;
      if (argText) {
        out = appendToServeCommand(out, `%%HL:${key}%%${argText}%%/HL:${key}%%`);
      }
      if (envLines.length) {
        const indent = out.match(/^[ \t]*(?=vllm serve\b)/m)?.[0] ?? '';
        const exports = envLines.map((l) => `%%HL:${key}%%${l}%%/HL:${key}%%`).join('\n' + indent);
        out = out.replace(/^([ \t]*)(vllm serve\b)/m, `$1${exports}\n$1$2`);
      }
    }
    return '```' + (lang || '') + '\n' + out + '```';
  });
}

// Substitute {{name}}:
//   feature toggle -> renderFeature (args/env or flag_when_false), colored
//   config value  -> render the value
export function applyConfigParams(
  content: string,
  params: Record<string, unknown>,
  toggleFeatures: Record<string, FeatureMeta>,
): string {
  return content.replace(/\{\{(\w+)\}\}/g, (_, name) => {
    const value = params[name];
    const feat = toggleFeatures[name];
    if (feat) {
      const text = renderFeature(feat, !!value);
      // Wrap in %%HL%% markers so the rendered command shows the flag in the
      // same color as its Config chip (stripped by stripRenderMarkers for the
      // copy button and by renderMarkdown's SSR pass).
      return text ? `%%HL:${name}%%${text}%%/HL:${name}%%` : '';
    }
    return String(value ?? '');
  });
}

function initialParamValues(
  params: Record<string, ConfigParam> | undefined,
  toggleFeatures: Record<string, FeatureMeta>,
  optInFeatures: string[] | undefined,
  featureModes: Record<string, 'toggle' | 'extra' | 'builtin'>,
): Record<string, unknown> {
  const init: Record<string, unknown> = {};
  for (const [key, p] of Object.entries(params ?? {})) {
    init[key] = p.default;
  }
  for (const key of Object.keys(toggleFeatures)) {
    init[key] = !(optInFeatures ?? []).includes(key);
  }
  // 'extra' features default OFF so the page first renders the exact
  // tutorial baseline; opting in appends their args.
  for (const [key, mode] of Object.entries(featureModes)) {
    if (mode === 'extra') init[key] = false;
  }
  return init;
}

// ---- Markdown renderer ----
function stripRenderMarkers(content: string): string {
  return content
    .replace(/%%CONFIG:\w[\w-]*%%|%%\/CONFIG:\w[\w-]*%%/g, '')
    .replace(/%%HL:\w[\w-]*%%|%%\/HL:\w[\w-]*%%/g, '');
}

/** Runtime env vars referenced by a step (`$FOO` / `${FOO}`, uppercase).
 *  Positional params ($1/$2) and lowercase shell vars are ignored. */
function runtimeEnvVarsOf(content: string): string[] {
  const vars = new Set<string>();
  for (const m of content.matchAll(/\$(\{)?([A-Z][A-Z0-9_]*)(\})?/g)) {
    vars.add(m[2]);
  }
  return [...vars].sort();
}

export function renderMarkdown(md: string): string {
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
          (_, label, url) =>
            `<a href="${resolveVllmAscendLink(url)}" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">${label}</a>`,
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
        (_, label, url) =>
          `<a href="${resolveVllmAscendLink(url)}" target="_blank" rel="noopener" class="text-accent-400 hover:text-accent-300 border-b border-accent-500/30">${label}</a>`,
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
  prefix_caching: 'text-emerald-400',
  speculative_config: 'text-amber-400',
  spec_decoding: 'text-amber-400',
  compilation_config: 'text-lime-400',
  async_scheduling: 'text-sky-400',
  npugraph_ex: 'text-violet-400',
  cpu_binding: 'text-cyan-400',
  'dsa-cp': 'text-orange-400',
  multistream_overlap: 'text-pink-400',
  tool_calling: 'text-teal-400',
  reasoning: 'text-fuchsia-400',
  expert_parallel: 'text-emerald-400',
  mlapo: 'text-orange-400',
};

// Friendly labels for config_param bool toggles (shown on the chip itself).
const CONFIG_LABELS: Record<string, string> = {
  prefix_caching: 'Prefix Caching',
  speculative_config: 'Speculative Config',
  spec_decoding: 'MTP Spec Decoding',
  compilation_config: 'Compilation Config',
  async_scheduling: 'Async Scheduling',
  flashcomm1: 'FlashComm1',
};

// Humanize a feature key for chips without an explicit label (expert_parallel
// -> "Expert Parallel").
function prettifyKey(key: string): string {
  return key
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

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
  configParamsEn,
  configParamsZh,
  featuresEn,
  featuresZh,
  optInFeaturesEn,
  optInFeaturesZh,
  variantsEn,
  variantsZh,
  hardwareStatus,
  selectorLabelsEn,
  selectorLabelsZh,
  pdCluster,
}: CascadeSelectorProps) {
  const { lang, t } = useLang();
  const scenarios = lang === 'zh' ? scenariosZh : scenariosEn;
  const extraConfig =
    lang === 'zh' ? (extraConfigZh ?? extraConfigEn) : (extraConfigEn ?? extraConfigZh);
  const selectorLabels =
    lang === 'zh' ? (selectorLabelsZh ?? selectorLabelsEn) : (selectorLabelsEn ?? selectorLabelsZh);
  const configParams =
    lang === 'zh' ? (configParamsZh ?? configParamsEn) : (configParamsEn ?? configParamsZh);
  const features = lang === 'zh' ? (featuresZh ?? featuresEn) : (featuresEn ?? featuresZh);
  const variants = lang === 'zh' ? (variantsZh ?? variantsEn) : (variantsEn ?? variantsZh);
  const optInFeatures =
    lang === 'zh' ? (optInFeaturesZh ?? optInFeaturesEn) : (optInFeaturesEn ?? optInFeaturesZh);
  // Config-panel toggles = features referenced by the recipe steps
  // ({{name}} placeholders or %%CONFIG%% markers). Default state comes from
  // upstream opt_in_features (absent = default-on).
  const referencedKeys = useMemo(() => {
    const set = new Set<string>();
    for (const s of scenarios) {
      for (const st of s.steps) {
        const content = expandScenarioScripts(st.content, s.scripts);
        for (const m of content.matchAll(/\{\{(\w+)\}\}/g)) set.add(m[1]);
        for (const m of content.matchAll(/%%CONFIG:([\w-]+)%%/g)) set.add(m[1]);
      }
    }
    return set;
  }, [scenarios]);
  const toggleFeatures = useMemo(() => {
    const out: Record<string, FeatureMeta> = {};
    for (const [key, f] of Object.entries(features ?? {})) {
      if (referencedKeys.has(key)) out[key] = f;
    }
    return out;
  }, [features, referencedKeys]);

  // Per-feature rendering mode, classifying how every step relates to it:
  //   'toggle'   — referenced via {{}}/%%CONFIG%% markers: the placeholder
  //                substitution owns rendering (both on and off states)
  //   'extra'    — never referenced and never hardcoded: default-off toggle;
  //                enabling appends the feature's args to the serve command
  //   'builtin'  — hardcoded in every step (or mixed hardcoding): shown
  //                always-on, not toggleable — matches the recipe baseline
  const featureModes = useMemo(() => {
    const out: Record<string, 'toggle' | 'extra' | 'builtin'> = {};
    for (const [key, f] of Object.entries(features ?? {})) {
      if (referencedKeys.has(key)) {
        out[key] = 'toggle';
        continue;
      }
      let present = 0;
      for (const s of scenarios) {
        for (const st of s.steps) {
          if (
            featureHandledIn(expandScenarioScripts(st.content, s.scripts), key, f) === 'present'
          ) {
            present++;
          }
        }
      }
      out[key] = present > 0 ? 'builtin' : 'extra';
    }
    return out;
  }, [features, scenarios, referencedKeys]);

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
  const effectiveCase = cases.includes(selectedCase) ? selectedCase : cases[0] || '';

  const currentScenario = scenarios.find(
    (s) =>
      s.npu === selectedNpu &&
      s.precision === effectivePrecision &&
      s.deployment === effectiveDeployment &&
      s.case === effectiveCase,
  );

  const effectiveConfigParams = useMemo(
    () => ({
      ...(configParams ?? {}),
      ...(currentScenario?.config_params ?? {}),
    }),
    [configParams, currentScenario],
  );

  const [paramValues, setParamValues] = useState<Record<string, unknown>>(() =>
    initialParamValues(effectiveConfigParams, toggleFeatures, optInFeatures, featureModes),
  );

  // Step tab state — reset when scenario changes (adjust during render, per
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  const [activeStep, setActiveStep] = useState(0);
  const [trackedScenario, setTrackedScenario] = useState(currentScenario);
  if (currentScenario !== trackedScenario) {
    setTrackedScenario(currentScenario);
    setActiveStep(0);
    setParamValues(
      initialParamValues(effectiveConfigParams, toggleFeatures, optInFeatures, featureModes),
    );
  }

  // PD-cluster interactive state: node index (within the active role) and
  // Cluster env endpoints (persisted in localStorage, like upstream).
  const [pdNodeIdx, setPdNodeIdx] = useState(0);
  const [pdEndpoints, setPdEndpoints] = useState<Record<string, string>>(loadPdEndpoints);
  const [showClusterEnv, setShowClusterEnv] = useState(false);
  if (currentScenario !== trackedScenario) {
    setPdNodeIdx(0);
  }

  // Extra config multi-select — initialize from current scenario defaults,
  // and re-sync when the scenario changes
  const [selectedConfigs, setSelectedConfigs] = useState<Set<string>>(() => {
    if (currentScenario?.default_configs) {
      return new Set(currentScenario.default_configs);
    }
    return new Set();
  });
  if (currentScenario !== trackedScenario) {
    setSelectedConfigs(
      currentScenario?.default_configs ? new Set(currentScenario.default_configs) : new Set(),
    );
  }

  const toggleConfig = (key: string) => {
    setSelectedConfigs((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const chipClass = (active: boolean) =>
    `px-3 py-1.5 text-xs font-mono rounded-md transition-all cursor-pointer inline-flex items-center ${
      active
        ? 'bg-accent-500/10 text-accent-400 border border-accent-500/30'
        : 'border border-ink-700/60 text-ink-400 hover:text-ink-200 hover:border-ink-600 bg-ink-900/50'
    }`;

  // Resolve rendered content for current step (hooks must run before any
  // early return to keep call order stable across renders)
  const currentStep = currentScenario?.steps[activeStep];
  const deployment = (currentScenario?.deployment || '').toLowerCase();
  const isPd =
    !!currentScenario &&
    (currentScenario.deployment === 'pd' ||
      currentScenario.strategy === 'pd_cluster' ||
      currentScenario.tags?.includes('pd-multinode') ||
      (deployment.includes('pd') &&
        (deployment.includes('multi') || deployment.includes('多节点'))));
  const hwKey = hwKeyForNpu(selectedNpu);
  const pdPrefillNodes = roleNodeCount(pdCluster?.prefill, hwKey);
  const pdDecodeNodes = roleNodeCount(pdCluster?.decode, hwKey);
  const pdRole: 'prefill' | 'decode' | null = currentStep
    ? currentStep.title.toLowerCase().includes('prefill')
      ? 'prefill'
      : currentStep.title.toLowerCase().includes('decode')
        ? 'decode'
        : null
    : null;
  const pdNodeCount =
    pdRole === 'prefill' ? pdPrefillNodes : pdRole === 'decode' ? pdDecodeNodes : 0;
  const setPdEndpoint = (key: string, value: string) => {
    setPdEndpoints((prev) => {
      const next = { ...prev };
      if (!value) delete next[key];
      else next[key] = value;
      savePdEndpoint(key, value);
      return next;
    });
  };

  // Effective %%CONFIG%% selection = legacy extra_config chips (dsa-cp) plus
  // any feature toggle whose key appears in the step's %%CONFIG%% markers
  // (cpu_binding / multistream_overlap / npugraph_ex are feature-driven).
  const effectiveConfigs = useMemo(() => {
    const merged = new Set(selectedConfigs);
    for (const key of Object.keys(toggleFeatures)) {
      if (paramValues[key]) merged.add(key);
      else merged.delete(key);
    }
    return merged;
  }, [selectedConfigs, paramValues, toggleFeatures]);

  const rawContent = useMemo(() => {
    if (!currentStep) return '';
    const expandedContent = expandScenarioScripts(currentStep.content, currentScenario?.scripts);
    const base = applyConfigReplace(
      applyConfigParams(expandedContent, paramValues, toggleFeatures),
      effectiveConfigs,
      currentStep.config_values,
    );
    // Enabled 'extra' features (never referenced by placeholders) append
    // their args/env to the rendered `vllm serve` commands.
    const extras: Array<[string, FeatureMeta]> = [];
    for (const [key, f] of Object.entries(features ?? {})) {
      if (featureModes[key] === 'extra' && paramValues[key]) extras.push([key, f]);
    }
    const withExtras = appendFeatureArgs(base, extras);
    return isPd
      ? substitutePdContent(
          withExtras,
          currentStep.title,
          pdNodeIdx,
          pdEndpoints,
          pdPrefillNodes,
          pdDecodeNodes,
        )
      : withExtras;
  }, [
    currentStep,
    currentScenario,
    effectiveConfigs,
    paramValues,
    toggleFeatures,
    features,
    featureModes,
    isPd,
    pdNodeIdx,
    pdEndpoints,
    pdPrefillNodes,
    pdDecodeNodes,
  ]);

  const renderedHtml = useMemo(() => {
    if (!rawContent) return '';
    const mdHtml = renderMarkdown(rawContent);
    return applyColorHighlights(mdHtml, effectiveConfigs);
  }, [rawContent, effectiveConfigs]);

  const runtimeVars = useMemo(() => {
    if (!currentStep) return [];
    const expandedContent = expandScenarioScripts(currentStep.content, currentScenario?.scripts);
    return runtimeEnvVarsOf(expandedContent);
  }, [currentStep, currentScenario]);

  if (npus.length === 0) return null;

  // ---- Filter rows (vllm recipes-style: status dots, VRAM badges, hover info) ----
  const rowShell = (
    label: string,
    hint: string | undefined,
    children: React.ReactNode,
    key: string,
  ) => (
    <div key={key} className="flex items-start gap-4 px-4 py-3 border-b border-ink-800/40">
      <span className="shrink-0 w-24 pt-0.5 text-xs font-mono text-ink-300 inline-flex items-center gap-1.5">
        {label}
        {hint && <RowHint text={hint} />}
      </span>
      <div className="flex flex-wrap gap-1.5 min-w-0">{children}</div>
    </div>
  );

  const npuRow = rowShell(
    selectorLabels?.npu ?? t('labelNpu'),
    undefined,
    npus.map((opt) => {
      const status = hardwareStatus?.[hwKeyForNpu(opt)];
      const tooltip = npuInfo(opt, lang);
      return (
        <Tooltip key={opt} content={tooltip}>
          <button onClick={() => setSelectedNpu(opt)} className={chipClass(selectedNpu === opt)}>
            <StatusDot status={status} />
            <span className="font-semibold">{opt}</span>
          </button>
        </Tooltip>
      );
    }),
    'npu',
  );

  const variantHint =
    lang === 'zh'
      ? '所示显存为加载模型（权重 + 运行时开销）的最低要求；服务还需额外 KV cache 显存 —— 长上下文/大并发场景通常需要 1.5–2 倍。'
      : 'VRAM shown is the minimum to LOAD the model (weights + runtime overhead). Serving needs extra NPU memory for KV cache — long context or large batch typically needs 1.5–2× more.';
  const precisionRow = rowShell(
    selectorLabels?.precision ?? t('labelPrecision'),
    variantHint,
    precisions.map((opt) => {
      const v = variantForPrecision(variants, opt);
      return (
        <Tooltip key={opt} content={variantTooltip(v, lang)}>
          <button
            onClick={() => setSelectedPrecision(opt)}
            className={chipClass(effectivePrecision === opt)}
          >
            <span className="font-semibold">{opt}</span>
            {typeof v?.vram_minimum_gb === 'number' && (
              <span className="ml-1.5 font-mono text-ink-500">{v.vram_minimum_gb} GB</span>
            )}
          </button>
        </Tooltip>
      );
    }),
    'precision',
  );

  const deploymentRow = rowShell(
    selectorLabels?.deployment ?? t('labelDeployment'),
    undefined,
    deployments.map((opt) => (
      <Tooltip key={opt} content={deploymentInfo(opt, lang)}>
        <button
          onClick={() => setSelectedDeployment(opt)}
          className={chipClass(effectiveDeployment === opt)}
        >
          <span className="font-semibold">{deploymentLabel(opt, lang)}</span>
        </button>
      </Tooltip>
    )),
    'deployment',
  );

  const caseRow = rowShell(
    selectorLabels?.case ?? t('labelCase'),
    undefined,
    cases.map((opt) => {
      const caseScenario = scenarios.find(
        (s) =>
          s.npu === selectedNpu &&
          s.precision === effectivePrecision &&
          s.deployment === effectiveDeployment &&
          s.case === opt,
      );
      const tooltip = caseScenario
        ? [
            ...(caseScenario.deployment === 'pd'
              ? [
                  lang === 'zh'
                    ? 'P 表示 Prefill，D 表示 Decode'
                    : 'P means Prefill; D means Decode',
                ]
              : []),
            ...caseScenario.steps.map((step, index) => `${index + 1}. ${step.title}`),
          ].join('\n')
        : undefined;
      return (
        <Tooltip key={opt} content={tooltip}>
          <button onClick={() => setSelectedCase(opt)} className={chipClass(effectiveCase === opt)}>
            <span className="font-semibold">{opt}</span>
          </button>
        </Tooltip>
      );
    }),
    'case',
  );

  // ---- Features row (all upstream features; vllm-style toggle pills) ----
  const featureEntries = Object.entries(features ?? {});
  const featureHint =
    lang === 'zh'
      ? '可选服务能力：开关会将对应参数注入下方命令或从中移除；已包含在教程基线命令中的选项显示为常开。'
      : 'Optional serving capabilities — toggling injects or removes the corresponding flags in the commands below. Options baked into the recipe baseline are shown always-on.';
  const featureTooltip = (_key: string, f: FeatureMeta, mode: string): string | undefined => {
    const parts: string[] = [];
    if (f.description) parts.push(f.description);
    const lines = [
      ...(f.args ?? []).map(renderArg),
      ...Object.entries(f.env ?? {}).map(([k, v]) => `export ${k}=${v}`),
    ];
    if (lines.length) parts.push(lines.join('\n'));
    if (mode === 'builtin') {
      parts.push(
        lang === 'zh'
          ? '已包含在本教程的基线命令中 —— 始终开启。'
          : "Included in this recipe's baseline commands — always on.",
      );
    } else if (mode === 'extra') {
      parts.push(
        lang === 'zh'
          ? '默认关闭；开启后会将上述参数追加到下方 vllm serve 命令。'
          : 'Off by default — enabling appends the flags above to the vllm serve command below.',
      );
    }
    return parts.length ? parts.join('\n\n') : undefined;
  };
  const featuresRow =
    featureEntries.length > 0
      ? rowShell(
          t('labelFeatures'),
          featureHint,
          featureEntries.map(([key, f]) => {
            const mode = featureModes[key] ?? 'extra';
            const isOn = mode === 'builtin' ? true : !!paramValues[key];
            const colorClass = CONFIG_COLORS[key] || 'text-ink-400';
            const bgClass = colorClass.replace('text-', 'bg-');
            return (
              <Tooltip key={key} content={featureTooltip(key, f, mode)}>
                <button
                  onClick={() =>
                    mode !== 'builtin' && setParamValues((prev) => ({ ...prev, [key]: !prev[key] }))
                  }
                  aria-disabled={mode === 'builtin'}
                  className={`px-3 py-1.5 text-xs font-mono rounded-md transition-all inline-flex items-center gap-1.5 ${
                    mode === 'builtin' ? 'cursor-default' : 'cursor-pointer'
                  } ${
                    isOn
                      ? `${colorClass} bg-accent-500/10 border border-current/30`
                      : 'border border-ink-700/60 text-ink-400 hover:text-ink-200 hover:border-ink-600 bg-ink-900/50'
                  }`}
                >
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${bgClass} ${isOn ? 'opacity-100' : 'opacity-30'}`}
                  ></span>
                  {f.label || CONFIG_LABELS[key] || prettifyKey(key)}
                </button>
              </Tooltip>
            );
          }),
          'features',
        )
      : null;

  // ---- Config row: editable value params + legacy extra_config chips ----
  const configHint =
    lang === 'zh'
      ? '可编辑参数，将替换到下方命令中（默认值与教程基线一致）。'
      : 'Editable values substituted into the commands below (defaults match the tutorial baseline).';
  const hasConfigRow =
    Object.keys(effectiveConfigParams).length > 0 || (extraConfig && extraConfig.length > 0);
  const configRow = hasConfigRow
    ? rowShell(
        t('labelConfig'),
        configHint,
        <>
          {/* Value params — editable inputs (defaults = tutorial) */}
          {Object.entries(effectiveConfigParams).map(([key, p]) =>
            p.type === 'bool' ? null : (
              <Tooltip key={key} content={p.description}>
                <div className="flex items-center gap-2 cursor-help">
                  <label className="text-xs font-mono text-ink-400">{key}</label>
                  <input
                    type="number"
                    step="any"
                    value={String(paramValues[key] ?? '')}
                    onChange={(e) =>
                      setParamValues((prev) => ({
                        ...prev,
                        [key]: e.target.value === '' ? '' : Number(e.target.value),
                      }))
                    }
                    className="w-24 rounded-md border border-ink-700 bg-ink-900 px-2 py-1 text-xs font-mono text-ink-200"
                  />
                </div>
              </Tooltip>
            ),
          )}

          {/* Legacy multi-select chips (dsa-cp — no upstream feature yet) */}
          {extraConfig &&
            extraConfig.map((cfg) => {
              const isSelected = selectedConfigs.has(cfg.key);
              const colorClass = CONFIG_COLORS[cfg.key] || 'text-ink-400';
              const bgClass = colorClass.replace('text-', 'bg-');
              return (
                <Tooltip key={cfg.key} content={cfg.label}>
                  <button
                    onClick={() => toggleConfig(cfg.key)}
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
                </Tooltip>
              );
            })}
        </>,
        'config',
      )
    : null;

  return (
    <div>
      {/* Filter panel */}
      <div className="rounded-lg border border-ink-800/60 bg-ink-900/70 overflow-hidden mb-8">
        {npuRow}
        {precisionRow}
        {deploymentRow}
        {caseRow}
        {featuresRow}
        {configRow}
      </div>

      {/* Result panel */}
      {currentScenario && (
        <div className="rounded-lg border border-ink-800/60 overflow-hidden">
          {/* Step tabs header */}
          <div className="flex items-center border-b border-ink-800/60 bg-ink-900/70">
            {currentScenario.tags && currentScenario.tags.length > 0 && (
              <span className="shrink-0 px-3 text-[10px] font-mono font-bold text-accent-400 uppercase tracking-wider">
                {currentScenario.tags.map((tag) => PIPELINE_LABELS[lang]?.[tag] || tag).join(' · ')}
              </span>
            )}
            <span className="shrink-0 px-3 text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider">
              {t('step') || 'Steps'}
            </span>
            {currentScenario.steps.map((_step, i) => (
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

          {/* PD-cluster controls: node selector + Cluster env (integrated into steps) */}
          {isPd && (
            <div className="border-b border-ink-800/60 bg-ink-900/40 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                {pdRole && (
                  <>
                    <span className="text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider">
                      {lang === 'zh' ? '节点' : 'Node'}
                    </span>
                    {Array.from({ length: pdNodeCount }, (_, i) => (
                      <button
                        key={i}
                        onClick={() => setPdNodeIdx(i)}
                        className={`px-3 py-1 text-xs font-mono rounded-md border transition-colors ${
                          pdNodeIdx === i
                            ? 'bg-accent-500/10 text-accent-400 border-accent-500/30'
                            : 'border-ink-700/60 text-ink-400 hover:text-ink-200'
                        }`}
                      >
                        {pdRole === 'prefill' ? 'p' : 'd'}
                        {i}
                      </button>
                    ))}
                    <span className="w-px h-4 bg-ink-700/60" />
                  </>
                )}
                <button
                  onClick={() => setShowClusterEnv((v) => !v)}
                  className={`px-3 py-1 text-xs font-mono rounded-md border transition-colors ${
                    showClusterEnv
                      ? 'bg-accent-500/10 text-accent-400 border-accent-500/30'
                      : 'border-ink-700/60 text-ink-400 hover:text-ink-200'
                  }`}
                >
                  Cluster env
                </button>
              </div>

              {showClusterEnv && (
                <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {[
                    {
                      key: 'IFACE_NAME',
                      label: lang === 'zh' ? 'Fabric NIC / 网卡名' : 'Fabric NIC / interface',
                    },
                    ...Array.from({ length: pdPrefillNodes }, (_, i) => ({
                      key: `PREFILL_NODE_${i + 1}`,
                      label: `Prefill Node ${i + 1} IP`,
                    })),
                    ...Array.from({ length: pdDecodeNodes }, (_, i) => ({
                      key: `DECODE_NODE_${i + 1}`,
                      label: `Decode Node ${i + 1} IP`,
                    })),
                  ].map((f) => (
                    <label
                      key={f.key}
                      className="flex items-center gap-2 text-xs font-mono text-ink-400"
                    >
                      <span className="shrink-0">${f.key}</span>
                      <input
                        value={pdEndpoints[f.key] || ''}
                        onChange={(e) => setPdEndpoint(f.key, e.target.value)}
                        placeholder={f.label}
                        className="w-full rounded-md border border-ink-700 bg-ink-900 px-2 py-1 text-xs font-mono text-ink-200"
                      />
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

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
              {runtimeVars.length > 0 && (
                <div className="ml-[22px] mt-4 rounded-md border border-ink-800/60 bg-ink-900/40 px-3 py-2 text-[11px] font-mono text-ink-400">
                  <span className="text-accent-400">
                    {lang === 'zh' ? '运行时环境变量' : 'Runtime environment variables'}:
                  </span>{' '}
                  {runtimeVars.join(', ')}
                  <span className="block mt-0.5 text-ink-600">
                    {lang === 'zh'
                      ? '由多节点部署环境注入；具体取值见 “Topology and variables” 步骤。'
                      : 'Injected by the multi-node deployment environment; see the "Topology and variables" step for details.'}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
