export interface WeightSource {
  source: string;
  url: string;
  command: string;
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

export interface Model {
  hf_id: string;
  title: string;
  provider: string;
  description: string;
  architecture: 'dense' | 'moe';
  parameters: string;
  active_parameters: string | null;
  context_length: number;
  modality: string;
  updated: string;

  overview: string;
  weight_download: WeightDownload[];
  quantization?: Quantization;
  prerequisites?: PrerequisiteItem[];
  env_setup: EnvSetup;
  scenarios: Scenario[];
  extra_config?: ExtraConfigItem[];
  performance?: PerformanceSection;
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
