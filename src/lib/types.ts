export interface Meta {
  title: string;
  slug: string;
  provider: string;
  description: string;
  date_added: string;
  date_updated?: string;
  difficulty?: 'beginner' | 'intermediate' | 'advanced';
  tasks?: string[];
  performance_headline?: string;
  related_recipes?: string[];
  hardware?: Record<string, 'verified' | 'unsupported' | 'experimental'>;
}

export interface ModelInfo {
  model_id: string;
  performance_model_names?: string[];
  min_vllm_version?: string;
  architecture: 'dense' | 'moe';
  parameter_count: string;
  active_parameters: string | null;
  context_length: number;
  modality: string;
}

export interface WeightSource {
  source: string;
  url: string;
  command: string;
}

export interface Reference {
  title: string;
  url: string;
}

export interface WeightDownload {
  weight_version: string;
  sources: WeightSource[];
}

export interface PrerequisiteItem {
  title: string;
  content: string;
}

export interface EnvSetupItem {
  content: string;
}

export interface ContainerEnv {
  [npu: string]: EnvSetupItem;
}

export interface EnvSetup {
  pip?: EnvSetupItem;
  container?: ContainerEnv;
}

export interface Quantization {
  content: string;
}

export interface ScenarioStep {
  title: string;
  content: string;
  config_values?: Record<string, { enabled: string; disabled: string }>;
}

export interface ScenarioScript {
  language: string;
  content: string;
}

export interface ExtraConfigItem {
  key: string;
  label: string;
}

export interface ScenarioSelectorLabels {
  npu?: string;
  precision?: string;
  deployment?: string;
  case?: string;
}

export interface Scenario {
  test_id?: string;
  npu: string;
  precision: string;
  deployment: string;
  case: string;
  tags?: string[];
  strategy?: string;
  npu_per_node?: number;
  aisbench?: Array<'accuracy' | 'performance'>;
  scripts?: Record<string, ScenarioScript>;
  steps: ScenarioStep[];
  default_configs?: string[];
  config_params?: Record<string, ConfigParam>;
}

export interface ConfigParam {
  default?: unknown;
  type?: 'number' | 'string' | 'bool';
  description?: string;
  flag?: string;
  flag_when_false?: string;
}

export interface FeatureMeta {
  label?: string;
  description?: string;
  args?: string[];
  env?: Record<string, string>;
  flag_when_false?: string;
}

// Upstream-aligned weight variant (vllm-project/recipes `variants:` block).
export interface Variant {
  model_id?: string;
  precision?: string;
  vram_minimum_gb?: number;
  description?: string;
  [key: string]: unknown;
}

export interface PerformanceSection {
  accuracy?: string;
  benchmark?: string;
}

export interface PerformanceTable {
  sheetName: string;
  headers: string[];
  rows: string[][];
}

export interface Evaluation {
  accuracy?: { content: string };
  performance?: { content: string };
}

export interface PdClusterRole {
  nodes?: number | { default?: number };
  parallelism?: string;
  vllm_args?: string[];
  env?: Record<string, string>;
}

export interface PdCluster {
  env?: Record<string, string>;
  prefill?: PdClusterRole;
  decode?: PdClusterRole;
}

export interface StrategyOverrides {
  pd_cluster?: PdCluster;
  [strategy: string]: unknown;
}

export interface Model {
  meta: Meta;
  model: ModelInfo;
  overview: string;
  weight_download: WeightDownload[];
  quantization?: Quantization;
  prerequisites?: PrerequisiteItem[];
  env_setup: EnvSetup;
  scenarios: Scenario[];
  extra_config?: ExtraConfigItem[];
  scenario_selector_labels?: ScenarioSelectorLabels;
  config_params?: Record<string, ConfigParam>;
  features?: Record<string, FeatureMeta>;
  opt_in_features?: string[];
  variants?: Record<string, Variant>;
  strategy_overrides?: StrategyOverrides;
  performance?: PerformanceSection;
  evaluation?: Evaluation;
  verification?: string;
  tuning?: string;
  faq?: string;
  references: Reference[];
  performance_model_names?: string[];

  _provider_slug: string;
  _model_slug: string;
  _yaml_path: string;
  /** True for converter input templates (template_*.yaml). */
  _is_template?: boolean;
}

export interface ModelListItem {
  hf_id: string;
  title: string;
  provider: string;
  description: string;
  architecture: string;
  parameters: string;
  active_parameters: string | null;
  context_length: number;
  modality: string;
  updated: string;
  url: string;
  json: string;
  npus: string[];
  precisions: string[];
  deployments: string[];
  /** Optional internal slug used by VerifyBadge / status hydration. */
  _model_slug?: string;
  /** True for converter input templates (shown separately on browse). */
  _is_template?: boolean;
  /** Optional absolute URL to `status/<slug>.json` (filled at JSON endpoint build time). */
  _status_url?: string;
}

export interface ProviderInfo {
  name: string;
  slug: string;
  count: number;
}
