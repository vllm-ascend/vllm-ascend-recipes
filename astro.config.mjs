// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';

const BASE = process.env.ASTRO_BASE || '/vllm-ascend-recipes';

// https://astro.build/config
export default defineConfig({
  site: 'https://vllm-ascend.github.io',
  base: BASE,
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()]
  }
});
