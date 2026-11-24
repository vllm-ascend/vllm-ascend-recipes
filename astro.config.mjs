// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';

const BASE = process.env.ASTRO_BASE || '/vllm-ascend-recipes';

// https://astro.build/config
export default defineConfig({
  site: 'https://vllm-ascend.github.io',
  base: BASE,
  redirects: {
    '/Qwen/Qwen3-0.6B': '/Qwen/Qwen3-Dense',
    '/Qwen/Qwen3-1.7B': '/Qwen/Qwen3-Dense',
    '/Qwen/Qwen3-4B': '/Qwen/Qwen3-Dense',
    '/Qwen/Qwen3-8B': '/Qwen/Qwen3-Dense',
    '/Qwen/Qwen3-14B': '/Qwen/Qwen3-Dense',
    '/Qwen/Qwen3-32B': '/Qwen/Qwen3-Dense',
  },
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
  },
});
