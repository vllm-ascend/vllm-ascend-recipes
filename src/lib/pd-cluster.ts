export const PD_ENDPOINTS_STORAGE_KEY = 'vllm-ascend-recipes:pd-endpoints';

export type PdNodes = number | { default?: number; [hw: string]: number | undefined };

export function roleNodeCount(cfg?: { nodes?: PdNodes }, hwKey?: string): number {
  if (!cfg?.nodes) return 1;
  if (typeof cfg.nodes === 'number') return cfg.nodes;
  if (hwKey && typeof cfg.nodes[hwKey] === 'number') return cfg.nodes[hwKey] as number;
  return cfg.nodes.default ?? 1;
}

export function loadPdEndpoints(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try {
    return JSON.parse(localStorage.getItem(PD_ENDPOINTS_STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

export function savePdEndpoint(key: string, value: string) {
  if (typeof window === 'undefined') return;
  try {
    const cur = loadPdEndpoints();
    if (!value) delete cur[key];
    else cur[key] = value;
    localStorage.setItem(PD_ENDPOINTS_STORAGE_KEY, JSON.stringify(cur));
  } catch {
    /* ignore */
  }
}

// Map 141.xx.xx.N / xx.xx.xx.N (last octet = pod index + 1, prefill first) to the
// matching node IP. Pod order matches node_entry.py: prefill pods come first, then
// decode pods.
export function fillDottedIps(
  text: string,
  endpoints: Record<string, string>,
  prefillNodes: number,
  decodeNodes: number,
): string {
  return text.replace(/\b(?:\d+\.)?(?:x+\.){2,3}(\d+)\b/g, (m, last: string) => {
    const pod = Number(last) - 1;
    let ip = '';
    if (pod >= 0 && pod < prefillNodes) {
      ip = endpoints[`PREFILL_NODE_${pod + 1}`] || '';
    } else if (pod >= prefillNodes && pod < prefillNodes + decodeNodes) {
      ip = endpoints[`DECODE_NODE_${pod - prefillNodes + 1}`] || '';
    }
    return ip ? ip : m;
  });
}

// Keep kv_port and engine_id unique within each role group.
export function bumpKvConfig(text: string, nodeIdx: number): string {
  return text
    .replace(
      /("kv_port"\s*:\s*")(\d+)(")/,
      (_m, a, num, c) => `${a}${Number(num) + nodeIdx * 100}${c}`,
    )
    .replace(
      /("engine_id"\s*:\s*")(\d+)(")/,
      (_m, a, num, c) => `${a}${Number(num) + nodeIdx}${c}`,
    );
}

export function substitutePdContent(
  content: string,
  stepTitle: string,
  nodeIdx: number,
  endpoints: Record<string, string>,
  prefillNodes: number,
  decodeNodes: number,
): string {
  let text = fillDottedIps(content, endpoints, prefillNodes, decodeNodes);

  // Named node-IP exports (GLM-5 style): fill the values the user already
  // entered in the Cluster env panel (PREFILL_NODE_1..N / DECODE_NODE_1..N;
  // GATEWAY falls back to PREFILL_NODE_1). Placeholders with no entry stay.
  text = text.replace(
    /export\s+(PREFILL|DECODE|GATEWAY)_NODE_(\d+)_IP="\$\{\1_NODE_\2_IP:-[^}]*\}"/g,
    (match, role: string, idx: string) => {
      const key = role === 'GATEWAY' ? 'PREFILL_NODE_1' : `${role}_NODE_${Number(idx) + 1}`;
      const ip = endpoints[key];
      return ip ? `export ${role}_NODE_${idx}_IP="${ip}"` : match;
    },
  );

  if (endpoints.IFACE_NAME) {
    text = text.replace(/(nic_name\s*=\s*)"[^"]*"/, `$1"${endpoints.IFACE_NAME}"`);
  }
  if (endpoints.PREFILL_NODE_1) {
    text = text.replace(/(node0_ip\s*=\s*)"[^"]*"/, `$1"${endpoints.PREFILL_NODE_1}"`);
  }
  if (endpoints.PREFILL_NODE_1) {
    text = text.replace(/<prefill_ip>/g, endpoints.PREFILL_NODE_1);
  }
  if (endpoints.DECODE_NODE_1) {
    text = text.replace(/<decode_ip>/g, endpoints.DECODE_NODE_1);
  }

  const t = stepTitle.toLowerCase();
  if (t.includes('prefill') || t.includes('decode')) {
    text = bumpKvConfig(text, nodeIdx);
  }
  return text;
}
