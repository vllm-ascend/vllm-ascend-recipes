import { z } from 'zod';

export const weightSourceSchema = z.object({
  source: z.string(),
  url: z.string(),
  command: z.string(),
});

export const weightDownloadSchema = z.object({
  weight_version: z.string(),
  sources: z.array(weightSourceSchema),
});

export const prerequisiteItemSchema = z.object({
  title: z.string(),
  content: z.string(),
});

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

export const quantizationSchema = z.object({
  content: z.string(),
});

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

export const scenarioSchema = z.object({
  npu: z.string(),
  precision: z.string(),
  deployment: z.string(),
  case: z.string(),
  steps: z.array(scenarioStepSchema),
  default_configs: z.array(z.string()).optional(),
});

export const referenceSchema = z.object({
  title: z.string(),
  url: z.string(),
});

export const performanceSectionSchema = z.object({
  accuracy: z.string().optional(),
  benchmark: z.string().optional(),
});

export const modelSchema = z.object({
  hf_id: z.string(),
  title: z.string(),
  provider: z.string(),
  description: z.string(),
  architecture: z.enum(['dense', 'moe']),
  parameters: z.string(),
  active_parameters: z.string().nullable(),
  context_length: z.number(),
  modality: z.string(),
  updated: z.string(),
  overview: z.string(),
  weight_download: z.array(weightDownloadSchema),
  quantization: quantizationSchema.optional(),
  prerequisites: z.array(prerequisiteItemSchema).optional(),
  env_setup: envSetupSchema,
  scenarios: z.array(scenarioSchema),
  extra_config: z.array(extraConfigItemSchema).optional(),
  performance: performanceSectionSchema.optional(),
  verification: z.string().optional(),
  tuning: z.string().optional(),
  faq: z.string().optional(),
  references: z.array(referenceSchema),
});

export type ModelSchema = z.infer<typeof modelSchema>;
