/**
 * Playwright globalTeardown: purge annotations written with `agent_id='e2e'`
 * from the canonical app.db.
 *
 * `desktop/tests/ohlcv-view.spec.ts` and `live-chart.spec.ts` write into the
 * same data dir the user's app reads from (per ADR-0020), so without a
 * teardown the e2e markers leak into the next interactive session. We tag
 * every test insert with `agent_id='e2e'`, which makes the cleanup a single
 * scoped DELETE that can't touch real agent-written annotations.
 *
 * Runs Python via `uv` so the path resolution stays in one place
 * (`default_app_data_dir()`); duplicating the platform-base algorithm in
 * Node would violate ADR-0020.
 */
import { spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..', '..')

export default async function globalTeardown() {
  const script = [
    'import sqlite3',
    'from market_analyser.config import default_app_data_dir',
    'db = default_app_data_dir() / "app.db"',
    'if not db.exists():',
    '    print(f"no app.db at {db}; nothing to purge")',
    'else:',
    '    con = sqlite3.connect(db)',
    '    try:',
    '        n = con.execute("DELETE FROM annotations WHERE agent_id = ?", ("e2e",)).rowcount',
    '        con.commit()',
    '        print(f"purged {n} e2e annotation row(s) from {db}")',
    '    finally:',
    '        con.close()',
  ].join('\n')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    stdio: 'inherit',
    shell: false,
  })
  if (result.status !== 0) {
    throw new Error(
      `playwright globalTeardown: purge failed (exit ${result.status ?? '?'})`,
    )
  }
}
