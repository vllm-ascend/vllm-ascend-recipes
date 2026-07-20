export interface Meta {
  title: string;
  slug: string;
  provider: string;
  description: string;
  date_added: string;
  tasks?: string[];
  performance_headline?: string;
  hardware?: Record<string, 'verified' | 'unsupported' | 'experimental'>;
}

export interface ModelInfo {
  model_id: string;
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

export interface ExtraConfigItem {
  key: string;
  label: string;
}

export interface Scenario {
  npu: string;
  precision: string;
  deployment: string;
  case: string;
  steps: ScenarioStep[];
  default_configs?: string[];
}

export interface PerformanceSection {
  accuracy?: string;
  benchmark?: string;
}

export interface Evaluation {
  accuracy?: { content: string };
  performance?: { content: string };
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
  performance?: PerformanceSection;
  evaluation?: Evaluation;
  verification?: string;
  tuning?: string;
  faq?: string;
  references: Reference[];

  _provider_slug: string;
  _model_slug: string;
  _yaml_path: string;
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
}

export interface ProviderInfo {
  name: string;
  slug: string;
  count: number;
}
