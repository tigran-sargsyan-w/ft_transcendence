import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['test/**/*.spec.ts'],

    coverage: {
      provider: 'v8',

      include: [
        'src/**/*.ts',
      ],

      exclude: [
        'src/main.ts',
        'src/types.ts',
      ],

      thresholds: {
          statements: 90,
          branches: 85,
          functions: 85,
          lines: 90,
      },
    },
  },
});