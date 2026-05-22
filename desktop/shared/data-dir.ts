/**
 * Canonical shared data directory between the Python sidecar and Electron.
 *
 * Per ADR-0020 the directory name is a contract — the literal string
 * `"market-analyser"` — and the algorithm below is implemented identically
 * here and in `src/market_analyser/config.py::default_app_data_dir()`. Drift
 * between the two resolvers is caught by `desktop/tests/main/data-dir.spec.ts`
 * and `tests/api/test_data_dir_contract.py`.
 *
 * This module deliberately does NOT import from `electron`. The data dir must
 * not depend on `app.getName()`, `app.getPath('userData')`, `package.json#name`,
 * or `build.productName`. Future renames of any of those identifiers cannot
 * move the shared dir without an explicit migration step.
 *
 * `MARKET_ANALYSER_DATA_DIR` is the verbatim override (no APP_DIRNAME suffix
 * appended) for tests and explicit-relocation use cases. Matches the Python
 * side semantics.
 */
import { homedir } from 'node:os'
import { join } from 'node:path'

export const APP_DIRNAME = 'market-analyser'
export const DATA_DIR_ENV_VAR = 'MARKET_ANALYSER_DATA_DIR'

export interface ResolveDeps {
  platform?: NodeJS.Platform
  env?: NodeJS.ProcessEnv
  homedir?: () => string
}

export function resolveSharedDataDir(deps: ResolveDeps = {}): string {
  const platform = deps.platform ?? process.platform
  const env = deps.env ?? process.env
  const home = deps.homedir ?? homedir

  const override = env[DATA_DIR_ENV_VAR]
  if (override) return override

  if (platform === 'win32') {
    const base = env.APPDATA ?? join(home(), 'AppData', 'Roaming')
    return join(base, APP_DIRNAME)
  }
  if (platform === 'darwin') {
    return join(home(), 'Library', 'Application Support', APP_DIRNAME)
  }
  const base = env.XDG_DATA_HOME ?? join(home(), '.local', 'share')
  return join(base, APP_DIRNAME)
}
