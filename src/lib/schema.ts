import { z } from 'zod';

// ========== Meta ==========
export const metaSchema = z.object({
  title: z.string(),
  slug: z.string(),
  provider: z.string(),
  description: z.string(),
  date_added: z.string(),
  date_updated: z.string().optional(),
  difficulty: z.enum(['beginner', 'intermediate', 'advanced']).optional(),
  tasks: z.array(z.string()).optional(),
  performance_headline: z.string().optional(),
  related_recipes: z.array(z.string()).optional(),
  hardware: z
    .object({
      atlas_800_a3: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      atlas_800_a2: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      mi300x: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      mi325x: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      mi355x: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      h200: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      b200: z.enum(['verified', 'unsupported', 'experimental']).optional(),
      gb200: z.enum(['verified', 'unsupported', 'experimental']).optional(),
    })
    .optional(),
});

// ========== Model ==========
export const modelInfoSchema = z.object({
  model_id: z.string(),
  performance_model_names: z.array(z.string().trim().min(1)).min(1).optional(),
  min_vllm_version: z.string().optional(),
  architecture: z.enum(['dense', 'moe']),
  parameter_count: z.string(),
  active_parameters: z.string().nullable(),
  context_length: z.number(),
  modality: z.string(),
  // Upstream-style fields (vllm-project/recipes), optional.
  base_args: z.array(z.string()).optional(),
  base_env: z.record(z.string(), z.string()).optional(),
  docker_image: z.union([z.string(), z.record(z.string(), z.string())]).optional(),
  nightly_required: z.boolean().optional(),
  install: z.record(z.string(), z.any()).optional(),
});

// ========== Weight Download ==========
export const weightSourceSchema = z.object({
  source: z.string(),
  url: z.string(),
  command: z.string(),
});

export const weightDownloadSchema = z.object({
  weight_version: z.string(),
  sources: z.array(weightSourceSchema),
});

// ========== Prerequisites ==========
export const prerequisiteItemSchema = z.object({
  title: z.string(),
  content: z.string(),
});

// ========== Env Setup ==========
export const envSetupItemSchema = z.object({
  content: z.string(),
});

export const containerEnvSchema = z.object({
  content: z.string(),
});

export const containerSchema = z.record(z.string(), containerEnvSchema);

export const envSetupSchema = z
  .object({
    pip: envSetupItemSchema.optional(),
    container: containerSchema.optional(),
  })
  .refine((data) => data.pip || data.container, {
    message: 'env_setup must have at least one of pip or container',
  });

// ========== Quantization ==========
export const quantizationSchema = z.object({
  content: z.string(),
});

// ========== Scenarios ==========
const configValueSchema = z.object({
  enabled: z.string(),
  disabled: z.string(),
});

export const scenarioStepSchema = z.object({
  title: z.string(),
  content: z.string(),
  config_values: z.record(z.string(), configValueSchema).optional(),
});

export const extraConfigItemSchema = z.object({
  key: z.string(),
  label: z.string(),
});

export const scenarioSelectorLabelsSchema = z.object({
  npu: z.string().optional(),
  precision: z.string().optional(),
  deployment: z.string().optional(),
  case: z.string().optional(),
});

export const scenarioSchema = z.object({
  npu: z.string(),
  precision: z.string(),
  deployment: z.string(),
  case: z.string(),
  // Pipeline-routing labels: a2-single / a3-single / pd-multinode, …
  tags: z.array(z.string()).optional(),
  steps: z.array(scenarioStepSchema),
  default_configs: z.array(z.string()).optional(),
});

// Configurable parameters whose defaults come from the tutorial baseline.
// Value params substitute {{name}}; boolean params render flag / flag_when_false.
export const configParamSchema = z.object({
  default: z.any(),
  type: z.enum(['number', 'string', 'bool']).optional(),
  description: z.string().optional(),
  flag: z.string().optional(),
  flag_when_false: z.string().optional(),
});

// ========== References ==========
export const referenceSchema = z.object({
  title: z.string(),
  url: z.string(),
});

// ========== Performance ==========
export const performanceSectionSchema = z.object({
  accuracy: z.string().optional(),
  benchmark: z.string().optional(),
});

// ========== Evaluation ==========
export const evaluationSchema = z.object({
  accuracy: z.object({ content: z.string() }).optional(),
  performance: z.object({ content: z.string() }).optional(),
});

// ========== Top-level Model ==========
export const modelSchema = z.object({
  meta: metaSchema,
  model: modelInfoSchema,
  overview: z.string(),
  weight_download: z.array(weightDownloadSchema),
  quantization: quantizationSchema.optional(),
  prerequisites: z.array(prerequisiteItemSchema).optional(),
  env_setup: envSetupSchema,
  scenarios: z.array(scenarioSchema),
  extra_config: z.array(extraConfigItemSchema).optional(),
  scenario_selector_labels: scenarioSelectorLabelsSchema.optional(),
  performance: performanceSectionSchema.optional(),
  evaluation: evaluationSchema.optional(),
  verification: z.string().optional(),
  tuning: z.string().optional(),
  faq: z.string().optional(),
  references: z.array(referenceSchema),
  // Upstream-style declarative fields (optional).
  features: z.record(z.string(), z.any()).optional(),
  opt_in_features: z.array(z.string()).optional(),
  variants: z.record(z.string(), z.any()).optional(),
  compatible_strategies: z.array(z.string()).optional(),
  strategy_overrides: z.record(z.string(), z.any()).optional(),
  hardware_overrides: z.record(z.string(), z.any()).optional(),
  dependencies: z.array(z.any()).optional(),
  // Configurable parameters with tutorial defaults.
  config_params: z.record(z.string(), configParamSchema).optional(),
});

export type ModelSchema = z.infer<typeof modelSchema>;
