import { useMemo, useState } from 'react';

interface PdRoleCfg {
  nodes?: number | { default?: number };
  vllm_args?: string[];
  env?: Record<string, string>;
}

interface PdCluster {
  env?: Record<string, string>;
  prefill?: PdRoleCfg;
  decode?: PdRoleCfg;
}

interface ScenarioStep {
  title: string;
  content: string;
}

interface PdClusterPanelProps {
  scenario: {
    npu: string;
    precision: string;
    deployment: string;
    case: string;
    steps: ScenarioStep[];
  };
  pdCluster: PdCluster | undefined;
  lang: 'en' | 'zh';
}

const STORAGE_KEY = 'vllm-ascend-recipes:pd-endpoints';

function extractBash(content: string): string {
  const m = content.match(/```(?:bash|shell)\s*\n([\s\S]*?)```/);
  return m ? m[1].trim() : '';
}

function launchLines(content: string): string[] {
  return extractBash(content)
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('python launch_online_dp.py'));
}

function roleNodes(cfg: PdRoleCfg | undefined): number {
  if (!cfg?.nodes) return 1;
  return typeof cfg.nodes === 'number' ? cfg.nodes : (cfg.nodes.default ?? 1);
}

function loadEndpoints(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function saveEndpoints(v: Record<string, string>) {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(v));
  } catch {
    /* ignore */
  }
}

// Mirrors scripts/multinode/node_entry.py fill_kv_config: kv_port +100 per node,
// engine_id +1 per node within the same role group.
function bumpKvConfig(text: string, nodeIdx: number): string {
  return text
    .replace(
      /("kv_port"\s*:\s*")(\d+)(")/,
      (_, a, num, c) => `${a}${Number(num) + nodeIdx * 100}${c}`,
    )
    .replace(/("engine_id"\s*:\s*")(\d+)(")/, (_, a, num, c) => `${a}${Number(num) + nodeIdx}${c}`);
}

function renderNodeScript(
  role: 'prefill' | 'decode',
  nodeIdx: number,
  template: string,
  endpoints: Record<string, string>,
): string {
  const baseKey = role === 'prefill' ? 'PREFILL' : 'DECODE';
  const ip = endpoints[`${baseKey}_NODE_${nodeIdx + 1}`] || '';

  let text = template;
  // local_ip="141.xx.xx.N" / "xx.xx.xx.N" → the node's real IP
  text = text.replace(
    /(local_ip\s*=\s*)"(?:\d+\.)?(?:x+\.){2,3}[\dXx]+"/,
    (_m, prefix) => `${prefix}"${ip}"`,
  );
  text = bumpKvConfig(text, nodeIdx);
  return text;
}

function renderLaunchCommand(
  role: 'prefill' | 'decode',
  nodeIdx: number,
  launch: string,
  endpoints: Record<string, string>,
): string {
  let text = launch;
  const baseKey = role === 'prefill' ? 'PREFILL' : 'DECODE';

  if (role === 'prefill') {
    const own = endpoints[`${baseKey}_NODE_${nodeIdx + 1}`] || '';
    text = text.replace(/--dp-address\s+\S+/, `--dp-address ${own}`);
  } else {
    const master = endpoints[`${baseKey}_NODE_1`] || '';
    const dpLocal = Number(text.match(/--dp-size-local\s+(\d+)/)?.[1] || 1);
    text = text
      .replace(/--dp-address\s+\S+/, `--dp-address ${master}`)
      .replace(/--dp-rank-start\s+\S+/, `--dp-rank-start ${nodeIdx * dpLocal}`);
  }
  return text;
}

export default function PdClusterPanel({ scenario, pdCluster, lang }: PdClusterPanelProps) {
  const t = lang === 'zh';

  const prefillTemplate = useMemo(
    () =>
      extractBash(
        scenario.steps.find((s) => s.title.toLowerCase().includes('prefill'))?.content ?? '',
      ),
    [scenario],
  );
  const decodeTemplate = useMemo(
    () =>
      extractBash(
        scenario.steps.find((s) => s.title.toLowerCase().includes('decode'))?.content ?? '',
      ),
    [scenario],
  );

  const launchCmds = useMemo(() => {
    const step = scenario.steps.find((s) => /launch|启动/i.test(s.title));
    return step ? launchLines(step.content) : [];
  }, [scenario]);
  const prefillLaunch = launchCmds.find((l) => l.includes('--dp-size 4')) ?? launchCmds[0] ?? '';
  const decodeLaunch =
    launchCmds.find((l) => l.includes('--dp-size 8')) ?? launchCmds[1] ?? 'VLLM_DECODE_LAUNCH';

  const prefillNodes = roleNodes(pdCluster?.prefill);
  const decodeNodes = roleNodes(pdCluster?.decode);

  const [role, setRole] = useState<'prefill' | 'decode'>('prefill');
  const [nodeIdx, setNodeIdx] = useState(0);
  const [endpoints, setEndpoints] = useState<Record<string, string>>(loadEndpoints);

  const maxIdx = (role === 'prefill' ? prefillNodes : decodeNodes) - 1;
  const safeIdx = Math.min(nodeIdx, Math.max(0, maxIdx));

  const setEndpoint = (key: string, value: string) => {
    setEndpoints((prev) => {
      const next = { ...prev };
      if (value === '') delete next[key];
      else next[key] = value;
      saveEndpoints(next);
      return next;
    });
  };

  const template = role === 'prefill' ? prefillTemplate : decodeTemplate;
  const launch = role === 'prefill' ? prefillLaunch : decodeLaunch;
  const renderedScript = renderNodeScript(role, safeIdx, template, endpoints);
  const renderedLaunch = renderLaunchCommand(role, safeIdx, launch, endpoints);

  const ipFields: { key: string; label: string }[] = [];
  for (let i = 0; i < prefillNodes; i++)
    ipFields.push({ key: `PREFILL_NODE_${i + 1}`, label: `Prefill Node ${i + 1} IP` });
  for (let i = 0; i < decodeNodes; i++)
    ipFields.push({ key: `DECODE_NODE_${i + 1}`, label: `Decode Node ${i + 1} IP` });

  const copy = (text: string) => {
    if (typeof navigator !== 'undefined') navigator.clipboard?.writeText(text);
  };

  return (
    <div className="rounded-lg border border-ink-800/60 overflow-hidden mb-6">
      {/* Role + node selector */}
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-800/60 bg-ink-900/70 px-3 py-2">
        <span className="text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider mr-1">
          {t ? '角色' : 'Role'}
        </span>
        {(['prefill', 'decode'] as const).map((r) => (
          <button
            key={r}
            onClick={() => {
              setRole(r);
              setNodeIdx(0);
            }}
            className={`px-3 py-1 text-xs font-mono rounded-md border transition-colors ${
              role === r
                ? 'bg-accent-500/10 text-accent-400 border-accent-500/30'
                : 'border-ink-700/60 text-ink-400 hover:text-ink-200'
            }`}
          >
            {r === 'prefill' ? 'Prefill' : 'Decode'}
          </button>
        ))}

        <span className="w-px h-5 bg-ink-700/60 mx-1" />
        <span className="text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider mr-1">
          {t ? '节点' : 'Node'}
        </span>
        {Array.from({ length: role === 'prefill' ? prefillNodes : decodeNodes }, (_, i) => (
          <button
            key={i}
            onClick={() => setNodeIdx(i)}
            className={`px-3 py-1 text-xs font-mono rounded-md border transition-colors ${
              safeIdx === i
                ? 'bg-accent-500/10 text-accent-400 border-accent-500/30'
                : 'border-ink-700/60 text-ink-400 hover:text-ink-200'
            }`}
          >
            {role === 'prefill' ? 'p' : 'd'}
            {i}
          </button>
        ))}
      </div>

      {/* Cluster env */}
      <div className="border-b border-ink-800/60 bg-ink-900/40 px-4 py-3">
        <div className="text-[10px] font-semibold text-ink-400 uppercase tracking-widest mb-2">
          Cluster env
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
          {ipFields.map((f) => (
            <label key={f.key} className="flex items-center gap-2 text-xs font-mono text-ink-400">
              <span className="shrink-0">${f.key}</span>
              <input
                value={endpoints[f.key] || ''}
                onChange={(e) => setEndpoint(f.key, e.target.value)}
                placeholder={f.label}
                className="w-full rounded-md border border-ink-700 bg-ink-900 px-2 py-1 text-xs font-mono text-ink-200"
              />
            </label>
          ))}
        </div>
        <div className="mt-2 text-[11px] text-ink-500">
          {t
            ? '填写各节点真实 IP 后，脚本中的 local_ip / kv_port / engine_id 会自动替换；留空则保留占位符。'
            : 'Fill in each node IP and the script will substitute local_ip / kv_port / engine_id; empty fields stay as placeholders.'}
        </div>
      </div>

      {/* Serve script */}
      <div className="px-4 py-4 bg-ink-950/60">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider">
            run_dp_template.sh
          </span>
          <button
            onClick={() => copy(renderedScript)}
            className="px-2 py-1 text-[10px] font-mono rounded border border-ink-700 bg-ink-800 text-ink-400 hover:text-accent-400"
          >
            {t ? '复制' : 'copy'}
          </button>
        </div>
        <pre className="overflow-x-auto text-xs font-mono text-ink-300 whitespace-pre rounded-md bg-ink-900/60 p-3">
          {renderedScript}
        </pre>
      </div>

      {/* Launch command */}
      <div className="px-4 py-4 bg-ink-950/60 border-t border-ink-800/40">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] font-mono font-bold text-ink-300 uppercase tracking-wider">
            launch_online_dp.py
          </span>
          <button
            onClick={() => copy(renderedLaunch)}
            className="px-2 py-1 text-[10px] font-mono rounded border border-ink-700 bg-ink-800 text-ink-400 hover:text-accent-400"
          >
            {t ? '复制' : 'copy'}
          </button>
        </div>
        <pre className="overflow-x-auto text-xs font-mono text-ink-300 whitespace-pre rounded-md bg-ink-900/60 p-3">
          {renderedLaunch}
        </pre>
      </div>
    </div>
  );
}
