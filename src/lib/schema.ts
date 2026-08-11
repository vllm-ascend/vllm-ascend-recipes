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

// ========== Upstream install (vllm-project/recipes) ==========
// install.pip / install.docker: `false` hides the tab, an object overrides the
// generated one-liner / adds a note.
export const installTabSchema = z.union([
  z.literal(false),
  z.object({
    command: z.string().optional(),
    note: z.string().optional(),
  }),
]);

export const installSchema = z
  .object({
    pip: installTabSchema.optional(),
    docker: installTabSchema.optional(),
  })
  .refine((data) => data.pip || data.docker, {
    message: 'install must configure at least one of pip/docker (or omit install entirely)',
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
  install: installSchema.optional(),
});

// ========== Upstream declarative fields (vllm-project/recipes) ==========
export const featureSchema = z
  .object({
    label: z.string().optional(),
    description: z.string().optional(),
    args: z.array(z.string()).optional(),
    env: z.record(z.string(), z.string()).optional(),
    // Page extension: text rendered when the toggle is OFF (e.g.
    // prefix_caching -> --no-enable-prefix-caching).
    flag_when_false: z.string().optional(),
  })
  .refine((f) => f.args || f.env, {
    message: 'feature must declare args or env',
  });

export const variantSchema = z.object({
  // Upstream enum: bf16|fp8|nvfp4|fp4|int4|int8|awq|gptq|mxfp4.
  // W8A8 / w8a8 kept as the Ascend extension (per repo convention).
  precision: z.string(),
  vram_minimum_gb: z.number().int().positive(),
  description: z.string(),
  model_id: z.string().optional(),
  supported_hardware: z.array(z.string()).optional(),
  extra_args: z.array(z.string()).optional(),
  extra_env: z.record(z.string(), z.string()).optional(),
});

export const dependencySchema = z.object({
  note: z.string(),
  command: z.string(),
  optional: z.boolean().optional(),
  brand: z.union([z.string(), z.array(z.string())]).optional(),
});

export const overrideSchema = z.object({
  tp: z.number().int().optional(),
  extra_args: z.array(z.string()).optional(),
  extra_env: z.record(z.string(), z.string()).optional(),
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
  // Interlocks with top-level compatible_strategies (single_node_A2 /
  // single_node_A3 / pd_cluster) and routes the CI pipeline.
  strategy: z.string().optional(),
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
export const modelSchema = z
  .object({
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
    // Upstream-style declarative fields (vllm-project/recipes).
    features: z.record(z.string(), featureSchema).optional(),
    opt_in_features: z.array(z.string()).optional(),
    variants: z.record(z.string(), variantSchema).optional(),
    compatible_strategies: z.array(z.string()).optional(),
    strategy_overrides: z.record(z.string(), overrideSchema).optional(),
    hardware_overrides: z.record(z.string(), overrideSchema).optional(),
    dependencies: z.array(dependencySchema).optional(),
    // Upstream tutorial body (kept as an empty placeholder here; the actual
    // tutorial content lives in overview/prerequisites/env_setup/scenarios).
    guide: z.string().optional(),
    guide_zh: z.string().optional(),
    // Configurable parameters with tutorial defaults (page/CI extension).
    config_params: z.record(z.string(), configParamSchema).optional(),
  })
  .superRefine((data, ctx) => {
    const featureKeys = new Set(Object.keys(data.features ?? {}));
    const configKeys = new Set(Object.keys(data.config_params ?? {}));
    const strategyList = data.compatible_strategies ?? [];
    const extraKeys = new Set((data.extra_config ?? []).map((e) => e.key));

    // opt_in_features must reference declared features.
    for (const key of data.opt_in_features ?? []) {
      if (!featureKeys.has(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['opt_in_features'],
          message: `opt_in_features references unknown feature '${key}'`,
        });
      }
    }

    // Upstream requires a `default` variant whenever variants is present.
    if (data.variants && !data.variants.default) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['variants'],
        message: 'variants must include a `default` variant',
      });
    }

    // Scenario-level interlock checks.
    for (const [si, sc] of (data.scenarios ?? []).entries()) {
      if (sc.strategy && strategyList.length > 0 && !strategyList.includes(sc.strategy)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['scenarios', si, 'strategy'],
          message: `scenario strategy '${sc.strategy}' is not listed in compatible_strategies`,
        });
      }
      for (const [ti, step] of sc.steps.entries()) {
        for (const m of step.content.matchAll(/\{\{(\w+)\}\}/g)) {
          if (!configKeys.has(m[1]) && !featureKeys.has(m[1])) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['scenarios', si, 'steps', ti, 'content'],
              message: `placeholder '{{${m[1]}}}' is not defined in config_params or features`,
            });
          }
        }
        for (const m of step.content.matchAll(/%%CONFIG:([\w-]+)%%/g)) {
          if (!extraKeys.has(m[1]) && !featureKeys.has(m[1])) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['scenarios', si, 'steps', ti, 'content'],
              message: `%%CONFIG:${m[1]}%% marker has no matching extra_config key or feature`,
            });
          }
        }
      }
    }
  });

export type ModelSchema = z.infer<typeof modelSchema>;
