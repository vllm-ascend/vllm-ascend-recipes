export const PD_ENDPOINTS_STORAGE_KEY = 'vllm-ascend-recipes:pd-endpoints';

export function roleNodeCount(cfg?: { nodes?: number | { default?: number } }): number {
  if (!cfg?.nodes) return 1;
  return typeof cfg.nodes === 'number' ? cfg.nodes : (cfg.nodes.default ?? 1);
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

// Map 141.xx.xx.N / xx.xx.xx.N (last octet 1..4) to the corresponding node IP:
// 1 → PREFILL_NODE_1, 2 → PREFILL_NODE_2, 3 → DECODE_NODE_1, 4 → DECODE_NODE_2.
export function fillDottedIps(text: string, endpoints: Record<string, string>): string {
  const ips = [1, 2, 3, 4].map((i) => {
    const role = i <= 2 ? 'PREFILL' : 'DECODE';
    const idx = i <= 2 ? i : i - 2;
    return endpoints[`${role}_NODE_${idx}`] || '';
  });
  return text.replace(/\b(?:\d+\.)?(?:x+\.){2,3}(\d+)\b/g, (m, last: string) => {
    const ip = ips[Number(last) - 1];
    return ip ? ip : m;
  });
}

// Mirrors scripts/multinode/node_entry.py fill_kv_config: kv_port +100 per node,
// engine_id +1 per node within the same role group.
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
): string {
  let text = fillDottedIps(content, endpoints);

  if (endpoints.IFACE_NAME) {
    text = text.replace(/(nic_name\s*=\s*)"[^"]*"/, `$1"${endpoints.IFACE_NAME}"`);
  }
  if (endpoints.PREFILL_NODE_1) {
    text = text.replace(/(node0_ip\s*=\s*)"[^"]*"/, `$1"${endpoints.PREFILL_NODE_1}"`);
  }

  const t = stepTitle.toLowerCase();
  if (t.includes('prefill') || t.includes('decode')) {
    text = bumpKvConfig(text, nodeIdx);
  }
  return text;
}
