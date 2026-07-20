// @ts-check
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import astro from 'eslint-plugin-astro';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default tseslint.config(
  // Base JS recommendations
  js.configs.recommended,

  // TypeScript
  ...tseslint.configs.recommended,
  {
    rules: {
      // Allow unused vars/args prefixed with _ (intentional API-surface holes)
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },

  // Astro (includes processor for .astro files + TS config for script blocks)
  ...astro.configs.recommended,

  // React (for .tsx/.jsx files)
  {
    files: ['**/*.tsx', '**/*.jsx'],
    languageOptions: {
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        console: 'readonly',
      },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    settings: {
      react: { version: '19' },
    },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // Astro projects don't need React in scope for JSX transform
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'off',
      // Reading persisted state from localStorage on mount is a common, accepted
      // pattern; downgrade the cascading-render warning so it doesn't block CI.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },

  // Node config files & scripts (process, __dirname, etc.)
  {
    files: ['*.mjs', '*.cjs', 'scripts/**/*.ts'],
    languageOptions: {
      globals: {
        ...globals.node,
        console: 'readonly',
      },
    },
  },

  // Prettier — must be last to disable conflicting formatting rules
  prettier,

  // Global ignores
  {
    ignores: ['dist/**', '.astro/**', 'node_modules/**', 'public/**', 'pnpm-lock.yaml'],
  },
);
