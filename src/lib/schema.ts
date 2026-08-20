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
      atlas_300i_duo: z.enum(['verified', 'unsupported', 'experimental']).optional(),
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

// Configurable parameters whose defaults come from the tutorial baseline.
// Value params substitute {{name}}; boolean params render flag / flag_when_false.
export const configParamSchema = z.object({
  default: z.any(),
  type: z.enum(['number', 'string', 'bool']).optional(),
  description: z.string().optional(),
  flag: z.string().optional(),
  flag_when_false: z.string().optional(),
});

export const scenarioSelectorLabelsSchema = z.object({
  npu: z.string().optional(),
  precision: z.string().optional(),
  deployment: z.string().optional(),
  case: z.string().optional(),
});

export const scenarioScriptSchema = z.object({
  language: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_+-]*$/),
  content: z.string(),
});

export const aisbenchTestSchema = z.enum(['accuracy', 'performance']);

export const scenarioSchema = z
  .object({
    test_id: z
      .string()
      .trim()
      .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, 'test_id must use lowercase kebab-case')
      .optional(),
    npu: z.string().trim().min(1),
    precision: z.string(),
    deployment: z.string(),
    case: z.string(),
    // Legacy CI routing fields are independent of the new template converter.
    tags: z.array(z.string()).optional(),
    strategy: z.string().optional(),
    npu_per_node: z.number().int().positive().optional(),
    aisbench: z.array(aisbenchTestSchema).optional(),
    scripts: z.record(z.string(), scenarioScriptSchema).optional(),
    steps: z.array(scenarioStepSchema),
    default_configs: z.array(z.string()).optional(),
    config_params: z.record(z.string(), configParamSchema).optional(),
  })
  .superRefine((scenario, ctx) => {
    // Scenarios using the structured script contract also use canonical,
    // machine-readable topology fields. Legacy display-only scenarios remain
    // valid until they are migrated to this contract.
    if (scenario.scripts !== undefined) {
      if (scenario.test_id === undefined) {
        ctx.addIssue({
          code: 'custom',
          path: ['test_id'],
          message: 'script-backed scenarios require test_id',
        });
      }
      if (scenario.npu_per_node === undefined) {
        ctx.addIssue({
          code: 'custom',
          path: ['npu_per_node'],
          message: 'script-backed scenarios require npu_per_node',
        });
      }
      if (!scenario.scripts['service-check']) {
        ctx.addIssue({
          code: 'custom',
          path: ['scripts'],
          message: 'script-backed scenarios require a service-check script',
        });
      }
      const deployment = String(scenario.deployment ?? '').toLowerCase();
      const isPd = deployment === 'pd' || (deployment !== 'non-pd' && deployment.includes('pd'));
      const isNonPd = deployment === 'non-pd';
      if (!isPd && !isNonPd) {
        ctx.addIssue({
          code: 'custom',
          path: ['deployment'],
          message:
            'script-backed scenarios require deployment to be "pd", "non-pd", ' +
            'or a legacy value carrying PD semantics (e.g. "Multi-Node PD Separation")',
        });
      } else {
        const validCase =
          deployment === 'pd'
            ? /^[1-9]\d*p[1-9]\d*d$/.test(scenario.case)
            : isPd
              ? /^[1-9]\d*[pP][1-9]\d*[dD]/.test(scenario.case)
              : /^[1-9]\d*-node$/.test(scenario.case);
        if (!validCase) {
          ctx.addIssue({
            code: 'custom',
            path: ['case'],
            message: isPd
              ? 'pd cases must match <positive integer>p<positive integer>d'
              : 'non-pd cases must match <positive integer>-node',
          });
        }
      }
    }

    const aisbenchTests = scenario.aisbench ?? [];
    if (new Set(aisbenchTests).size !== aisbenchTests.length) {
      ctx.addIssue({
        code: 'custom',
        path: ['aisbench'],
        message: 'aisbench test types must be unique',
      });
    }

    for (const [name, script] of Object.entries(scenario.scripts ?? {})) {
      if (script.content.includes('{{script:')) {
        ctx.addIssue({
          code: 'custom',
          path: ['scripts', name, 'content'],
          message: 'scenario scripts cannot reference other scenario scripts',
        });
      }
    }

    scenario.steps.forEach((step, stepIndex) => {
      const referencePattern = /\{\{script:([^{}]*)\}\}/g;
      for (const match of step.content.matchAll(referencePattern)) {
        const name = match[1];
        if (name && !scenario.scripts?.[name]) {
          ctx.addIssue({
            code: 'custom',
            path: ['steps', stepIndex, 'content'],
            message: `unknown scenario script reference: ${name}`,
          });
        }
      }
      const withoutValidReferences = step.content.replace(/\{\{script:([^{}]+)\}\}/g, '');
      if (withoutValidReferences.includes('{{script:')) {
        ctx.addIssue({
          code: 'custom',
          path: ['steps', stepIndex, 'content'],
          message: 'malformed scenario script reference',
        });
      }
    });
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
    // Upstream-style declarative fields (optional).
    features: z.record(z.string(), z.any()).optional(),
    opt_in_features: z.array(z.string()).optional(),
    variants: z.record(z.string(), z.any()).optional(),
    compatible_strategies: z.array(z.string()).optional(),
    strategy_overrides: z.record(z.string(), z.any()).optional(),
    hardware_overrides: z.record(z.string(), z.any()).optional(),
    dependencies: z.array(z.any()).optional(),
    // Upstream tutorial body (vllm-project/recipes `guide` field).
    guide: z.string().optional(),
    performance_model_names: z.array(z.string().trim().min(1)).min(1).optional(),
    guide_zh: z.string().optional(),
    // Configurable parameters with tutorial defaults.
    config_params: z.record(z.string(), configParamSchema).optional(),
  })
  .superRefine((recipe, ctx) => {
    const seenTestIds = new Map<string, number>();
    recipe.scenarios.forEach((scenario, scenarioIndex) => {
      if (scenario.test_id === undefined) return;
      const firstIndex = seenTestIds.get(scenario.test_id);
      if (firstIndex !== undefined) {
        ctx.addIssue({
          code: 'custom',
          path: ['scenarios', scenarioIndex, 'test_id'],
          message: `duplicate test_id: ${scenario.test_id} (first declared at scenarios[${firstIndex}])`,
        });
      } else {
        seenTestIds.set(scenario.test_id, scenarioIndex);
      }
    });
  });

export type ModelSchema = z.infer<typeof modelSchema>;
