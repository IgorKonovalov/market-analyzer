import type { Config } from 'jest'

const config: Config = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  roots: ['<rootDir>/renderer', '<rootDir>/shared'],
  testMatch: [
    '**/utils/*.{test,spec}.ts?(x)',
    '**/hooks/*.{test,spec}.ts?(x)',
    '**/store/**/*.{test,spec}.ts?(x)',
    '**/*.{test,spec}.ts?(x)',
  ],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/renderer/$1',
    '^@shared/(.*)$': '<rootDir>/shared/$1',
    '\\.(css|less|scss)$': '<rootDir>/tests/__mocks__/styleMock.cjs',
    // lightweight-charts v5 is ESM-only; jest's CJS resolver can't load it. Map to a
    // shared manual mock (Plan 0095). Per-file `jest.mock('lightweight-charts', …)`
    // factories in the component suites still override this for their file.
    '^lightweight-charts$': '<rootDir>/renderer/tests/lightweightChartsMock.ts',
  },
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        tsconfig: '<rootDir>/tsconfig.test.json',
        useESM: false,
      },
    ],
  },
}

export default config
