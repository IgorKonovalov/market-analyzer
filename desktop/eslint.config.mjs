import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import reactPlugin from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import prettierConfig from 'eslint-config-prettier'
import noUnkeyedLiterals from './eslint-rules/no-unkeyed-literals.mjs'

// Deliberately un-keyed literals (Plan 0069 phase 2): language endonyms (a
// language is named in its own script regardless of locale), the brand name,
// and unit / version tokens that are identical across locales.
const I18N_ALLOWLIST = ['English', 'Русский', 'market-analyser', 'UTC', 'v']

export default [
  {
    ignores: ['dist/**', 'release/**', 'node_modules/**', 'scripts/**/*.mjs', '**/*.cjs'],
  },
  {
    files: ['**/*.ts', '**/*.tsx'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      react: reactPlugin,
      'react-hooks': reactHooks,
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/no-explicit-any': 'warn',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  {
    // The i18n guard applies only to user-facing renderer surfaces (Plan 0069
    // phase 2), never to tests, hooks, lib, or the catalogs themselves.
    files: ['renderer/views/**/*.tsx', 'renderer/components/**/*.tsx', 'renderer/App.tsx'],
    ignores: ['**/*.test.tsx'],
    plugins: {
      i18n: { rules: { 'no-unkeyed-literals': noUnkeyedLiterals } },
    },
    rules: {
      'i18n/no-unkeyed-literals': ['error', { allow: I18N_ALLOWLIST }],
    },
  },
  prettierConfig,
]
