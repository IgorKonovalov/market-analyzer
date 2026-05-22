/**
 * Plan 0007 phase 4.1 done-when: the TypeScript half of ADR-0020.
 *
 * `resolveSharedDataDir()` computes the canonical shared data dir directly,
 * without going through `app.getName()` / `app.getPath('userData')` /
 * `package.json#name` / `build.productName`. The literal "market-analyser" is
 * the contract. `MARKET_ANALYSER_DATA_DIR` is the verbatim override.
 *
 * The cross-resolver consistency test (running the Python resolver from a
 * Node subprocess) is intentionally NOT here — the plan flags it as a
 * belt-and-braces check that may be too fragile to ship and notes the per-side
 * tests are the primary defence. This file is the per-side TypeScript defence;
 * `tests/api/test_data_dir_contract.py` is the Python side.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { APP_DIRNAME, DATA_DIR_ENV_VAR, resolveSharedDataDir } from '../../shared/data-dir'

const FAKE_HOME = '/home/test'

function makeHome(): () => string {
  return () => FAKE_HOME
}

describe('resolveSharedDataDir', () => {
  it('returns %APPDATA%/market-analyser on win32 when APPDATA is set', () => {
    const result = resolveSharedDataDir({
      platform: 'win32',
      env: { APPDATA: 'C:\\Users\\test\\AppData\\Roaming' },
      homedir: makeHome(),
    })
    expect(result).toBe(join('C:\\Users\\test\\AppData\\Roaming', APP_DIRNAME))
    expect(result.endsWith(APP_DIRNAME)).toBe(true)
  })

  it('falls back to <home>/AppData/Roaming/market-analyser on win32 when APPDATA is missing', () => {
    const result = resolveSharedDataDir({
      platform: 'win32',
      env: {},
      homedir: makeHome(),
    })
    expect(result).toBe(join(FAKE_HOME, 'AppData', 'Roaming', APP_DIRNAME))
    expect(result.endsWith(APP_DIRNAME)).toBe(true)
  })

  it('returns ~/Library/Application Support/market-analyser on darwin', () => {
    const result = resolveSharedDataDir({
      platform: 'darwin',
      env: {},
      homedir: makeHome(),
    })
    expect(result).toBe(join(FAKE_HOME, 'Library', 'Application Support', APP_DIRNAME))
    expect(result.endsWith(APP_DIRNAME)).toBe(true)
  })

  it('uses XDG_DATA_HOME on linux when set', () => {
    const result = resolveSharedDataDir({
      platform: 'linux',
      env: { XDG_DATA_HOME: '/custom/xdg' },
      homedir: makeHome(),
    })
    expect(result).toBe(join('/custom/xdg', APP_DIRNAME))
    expect(result.endsWith(APP_DIRNAME)).toBe(true)
  })

  it('falls back to ~/.local/share/market-analyser on linux when XDG_DATA_HOME is unset', () => {
    const result = resolveSharedDataDir({
      platform: 'linux',
      env: {},
      homedir: makeHome(),
    })
    expect(result).toBe(join(FAKE_HOME, '.local', 'share', APP_DIRNAME))
    expect(result.endsWith(APP_DIRNAME)).toBe(true)
  })

  it('every platform branch ends in the literal "market-analyser" dirname', () => {
    const platforms: NodeJS.Platform[] = ['win32', 'darwin', 'linux']
    for (const platform of platforms) {
      const result = resolveSharedDataDir({
        platform,
        env: platform === 'win32' ? { APPDATA: 'C:\\anywhere' } : {},
        homedir: makeHome(),
      })
      expect(result.endsWith(APP_DIRNAME)).toBe(true)
    }
    // Lock the contract value too — drift here means ADR-0020's contract moved.
    expect(APP_DIRNAME).toBe('market-analyser')
  })

  it('MARKET_ANALYSER_DATA_DIR is verbatim (no APP_DIRNAME suffix appended)', () => {
    const override = '/tmp/specific'
    const result = resolveSharedDataDir({
      platform: 'linux',
      env: { [DATA_DIR_ENV_VAR]: override, XDG_DATA_HOME: '/should/be/ignored' },
      homedir: makeHome(),
    })
    expect(result).toBe(override)
    expect(result.endsWith(APP_DIRNAME)).toBe(false)
  })

  it('MARKET_ANALYSER_DATA_DIR wins over every platform branch', () => {
    const override = '/tmp/override-wins'
    for (const platform of ['win32', 'darwin', 'linux'] as const) {
      const result = resolveSharedDataDir({
        platform,
        env: {
          [DATA_DIR_ENV_VAR]: override,
          APPDATA: 'C:\\should\\be\\ignored',
          XDG_DATA_HOME: '/should/be/ignored',
        },
        homedir: makeHome(),
      })
      expect(result).toBe(override)
    }
  })

  it('does NOT import electron (no app.getName / app.getPath dependency)', () => {
    // Per ADR-0020 the resolver lives under `desktop/shared/` so it must be
    // electron-free by construction. A regex on the source file is the
    // structural defence — if a future edit adds `from 'electron'`, this test
    // fails before the runtime regression bites.
    const src = readFileSync(join(__dirname, '..', '..', 'shared', 'data-dir.ts'), 'utf-8')
    expect(src).not.toMatch(/from\s+['"]electron['"]/)
    expect(src).not.toMatch(/require\(\s*['"]electron['"]\s*\)/)
  })
})
