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
