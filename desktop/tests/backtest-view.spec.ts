/**
 * Plan 0008 phase 5 — Backtest results view e2e.
 *
 * Two paths to BacktestView, covered by two specs:
 *   1. **Auto-route**: a `run.completed v1` envelope (kind=backtest) makes
 *      the renderer auto-swap to BacktestView with the new run_id, and the
 *      view renders within 3 s of the envelope. Driven by the e2e seam
 *      `window.__test_publish_run_completed__` so the test does not depend
 *      on cached Yahoo bars or on a live MCP `run_backtest` call.
 *   2. **Click-through**: RecentBacktestsView lists at least one persisted
 *      run; clicking the row opens BacktestView for that `run_id`.
 *
 * Both rely on a Python subprocess that seeds a deterministic
 * `BacktestResult` onto the sidecar's `runs/` directory + SQLite index,
 * pointed at the SAME `MARKET_ANALYSER_DATA_DIR` Electron's main process
 * passed to its spawned sidecar. The pattern mirrors `ohlcv-view.spec.ts`'s
 * `insertAnnotation`.
 */
import { _electron as electron, test, expect, type ElectronApplication } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..', '..')

interface SeedArgs {
  run_id: string
  strategy_id: string
  symbol: string
  timeframe: string
  total_return: number
  sharpe: number
  max_drawdown: number
  win_rate: number
  trade_count: number
  buy_and_hold_return: number
  initial_capital: number
  entry_price: number
  exit_price: number
}

/**
 * Seed a deterministic BacktestResult onto disk + SQLite by spawning a
 * Python subprocess that imports `persist()` directly. Returns the same
 * run_id the caller passed.
 *
 * `dataDir` MUST be the live Electron's `app.getPath('userData')` — that's
 * the directory the running sidecar's data layer reads from (Plan 0007 phase
 * 1 made Electron pass `MARKET_ANALYSER_DATA_DIR=<userData>` into the
 * spawned sidecar; under `_electron.launch` the default Python data dir and
 * the Electron userData dir diverge).
 */
function seedBacktest(args: SeedArgs, dataDir: string): string {
  const script = [
    'import json, sys',
    'from datetime import datetime, timedelta, timezone',
    'from pathlib import Path',
    'from market_analyser.backtest.persistence import persist',
    'from market_analyser.backtest.result import BacktestMetrics, BacktestResult, EquityPoint',
    'from market_analyser.backtest.types import Trade',
    'from market_analyser.config import default_app_data_dir',
    'from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory',
    'from market_analyser.persistence.repositories.backtest_runs import BacktestRunsRepository',
    'raw = json.loads(sys.stdin.read())',
    'data_dir = default_app_data_dir()',
    'data_dir.mkdir(parents=True, exist_ok=True)',
    'engine = make_engine(data_dir / "app.db")',
    'apply_migrations(engine)',
    'session_factory = make_session_factory(engine)',
    'repo = BacktestRunsRepository(session_factory)',
    'runs_dir = data_dir / "runs"',
    'runs_dir.mkdir(parents=True, exist_ok=True)',
    'start = datetime(2026, 4, 20, tzinfo=timezone.utc)',
    'equity_curve = [',
    '    EquityPoint(ts=start, equity=raw["initial_capital"]),',
    '    EquityPoint(ts=start+timedelta(days=1), equity=raw["initial_capital"] * (1 + raw["total_return"] * 0.5)),',
    '    EquityPoint(ts=start+timedelta(days=2), equity=raw["initial_capital"] * (1 + raw["total_return"])),',
    ']',
    'trade = Trade(entry_bar_index=0, exit_bar_index=1, entry_price=raw["entry_price"], exit_price=raw["exit_price"], kind="long")',
    'metrics = BacktestMetrics(',
    '    total_return=raw["total_return"],',
    '    sharpe=raw["sharpe"],',
    '    max_drawdown=raw["max_drawdown"],',
    '    max_drawdown_duration_bars=1,',
    '    win_rate=raw["win_rate"],',
    '    trade_count=raw["trade_count"],',
    '    buy_and_hold_return=raw["buy_and_hold_return"],',
    ')',
    'now = datetime.now(timezone.utc)',
    'result = BacktestResult(',
    '    run_id=raw["run_id"], engine_version="0.1.0",',
    '    strategy_id=raw["strategy_id"], strategy_version="0.1.0",',
    '    symbol=raw["symbol"], timeframe=raw["timeframe"],',
    '    range_start=start, range_end=start+timedelta(days=2),',
    '    bars_hash="e2e-fixture-hash",',
    '    params={"period": 14}, costs={"commission_bps": 0.0, "slippage_bps": 0.0},',
    '    initial_capital=raw["initial_capital"], sizing="fixed_fraction",',
    '    started_at=now, finished_at=now,',
    '    trades=[trade], equity_curve=equity_curve, metrics=metrics,',
    ')',
    'persist(result, runs_dir, repo)',
    'print(result.run_id)',
  ].join('\n')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    input: JSON.stringify(args),
    encoding: 'utf-8',
    shell: false,
    env: { ...process.env, MARKET_ANALYSER_DATA_DIR: dataDir },
  })
  if (result.status !== 0) {
    throw new Error(`seedBacktest failed (exit ${result.status}): ${result.stderr}`)
  }
  return result.stdout.trim()
}

async function getDataDir(app: ElectronApplication): Promise<string> {
  return app.evaluate(({ app: electronApp }) => electronApp.getPath('userData'))
}

async function launchApp(): Promise<ElectronApplication> {
  return electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
}

function freshRunId(prefix: string): string {
  // 32-char hex run_id, deterministic enough for `${prefix}…` matching but
  // unique per test invocation so reruns don't collide with prior artifacts.
  const ts = Date.now().toString(16).padStart(12, '0')
  const tail = Math.random().toString(16).slice(2, 22).padEnd(20, '0')
  return `${prefix}${(ts + tail).slice(0, 32 - prefix.length)}`
}

test('run.completed v1 (kind=backtest) auto-routes to BacktestView within 3 s', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })
  // Wait for the e2e seam to attach (App effect runs after mount).
  await expect
    .poll(
      () =>
        window.evaluate(
          () =>
            typeof (globalThis as { __test_publish_run_completed__?: unknown })
              .__test_publish_run_completed__ === 'function',
        ),
      { timeout: 5_000, intervals: [100] },
    )
    .toBe(true)

  const dataDir = await getDataDir(app)
  const runId = freshRunId('e2eauto')

  seedBacktest(
    {
      run_id: runId,
      strategy_id: 'rsi',
      symbol: 'AAPL',
      timeframe: '1d',
      total_return: 0.1234,
      sharpe: 1.5,
      max_drawdown: -0.05,
      win_rate: 1.0,
      trade_count: 1,
      buy_and_hold_return: 0.08,
      initial_capital: 10_000,
      entry_price: 100,
      exit_price: 110,
    },
    dataDir,
  )

  await window.evaluate((id) => {
    const fn = (
      globalThis as {
        __test_publish_run_completed__?: (p: {
          kind: 'backtest' | 'analysis' | 'defi'
          run_id: string
          artifact_path: string
        }) => void
      }
    ).__test_publish_run_completed__
    if (!fn) throw new Error('__test_publish_run_completed__ not attached')
    fn({ kind: 'backtest', run_id: id, artifact_path: id })
  }, runId)

  // The bounded `toBeVisible({ timeout })` is itself the "renders promptly"
  // check — the view must appear within 3s of the run.completed event. (A
  // separate wall-clock `elapsed < 3000` assertion was redundant and mildly
  // flake-prone under CI scheduling jitter; dropped in Plan 0072 phase 7.)
  await expect(window.getByTestId('backtest-title')).toBeVisible({ timeout: 3_000 })

  // Header contains strategy + symbol + timeframe.
  const titleText = await window.getByTestId('backtest-title').textContent()
  expect(titleText).toContain('rsi v0.1.0')
  expect(titleText).toContain('AAPL')
  expect(titleText).toContain('1d')

  // Total return matches the seeded fixture (+12.34%, signed +, two decimals).
  // The testid wraps both the dt label and dd value, so we match by substring.
  await expect(window.getByTestId('metric-total-return')).toContainText('+12.34%')

  // Equity curve series visible (asserted via the test hook, not canvas pixels).
  interface BacktestStateSnapshot {
    run_id: string
    strategy_id: string
    symbol: string
    timeframe: string
    equityCurve: Array<{ time: number; value: number }>
    initial_capital: number
  }
  await expect
    .poll(
      () =>
        window.evaluate(
          () =>
            (globalThis as { __test_backtest_state__?: { equityCurve?: unknown[] } })
              .__test_backtest_state__?.equityCurve?.length ?? 0,
        ),
      { timeout: 3_000, intervals: [100] },
    )
    .toBe(3)
  const eqState = (await window.evaluate(
    () => (globalThis as { __test_backtest_state__?: unknown }).__test_backtest_state__,
  )) as BacktestStateSnapshot | undefined
  expect(eqState?.run_id).toBe(runId)
  expect(eqState?.initial_capital).toBe(10_000)

  await app.close()
})

test('RecentBacktestsView lists seeded runs and click-through opens BacktestView', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  const dataDir = await getDataDir(app)
  const runId = freshRunId('e2eclk')

  seedBacktest(
    {
      run_id: runId,
      strategy_id: 'rsi',
      symbol: 'MSFT',
      timeframe: '1d',
      total_return: -0.0567,
      sharpe: -0.42,
      max_drawdown: -0.12,
      win_rate: 0.5,
      trade_count: 2,
      buy_and_hold_return: 0.02,
      initial_capital: 10_000,
      entry_price: 250,
      exit_price: 240,
    },
    dataDir,
  )

  await window.getByTestId('nav-backtests').click()
  await expect(window.getByRole('region', { name: 'Recent backtests' })).toBeVisible({
    timeout: 5_000,
  })

  // The seeded row appears in the list. Match by data-run-id since multiple
  // dev sessions may have left other rows in this user's data dir.
  const row = window.locator(`[data-testid="recent-row"][data-run-id="${runId}"]`)
  await expect(row).toBeVisible({ timeout: 5_000 })

  await row.click()

  await expect(window.getByTestId('backtest-title')).toBeVisible({ timeout: 5_000 })
  const titleText = await window.getByTestId('backtest-title').textContent()
  expect(titleText).toContain('MSFT')
  expect(titleText).toContain('rsi v0.1.0')

  // The negative total return renders with a "-" sign and two decimals.
  await expect(window.getByTestId('metric-total-return')).toContainText('-5.67%')

  // Back button returns to Recent Backtests.
  await window.getByTestId('backtest-back').click()
  await expect(window.getByRole('region', { name: 'Recent backtests' })).toBeVisible({
    timeout: 2_000,
  })

  await app.close()
})
