/**
 * Plan 0001 phase 5 done-when: candlestick chart for one symbol.
 *
 * Asserts the chart canvas appears after cold launch with the default symbol.
 * If the sidecar returns a non-200 (Yahoo down, cache miss with no network),
 * the renderer shows the error state instead — also captured so the spec
 * doesn't hang on a flake.
 *
 * Requires a built desktop bundle. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, test, expect, type ElectronApplication } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..', '..')

interface InsertAnnotationArgs {
  symbol: string
  timeframe: string
  event_ts: string
  kind: 'bullish_marker' | 'bearish_marker'
  label: string
}

/**
 * Insert an annotation into the sidecar's SQLite DB via a Python subprocess
 * that targets the SAME data dir the running sidecar is using. Plan 0006
 * phase 6 uses this to seed annotations without going through the MCP
 * transport.
 *
 * `dataDir` MUST be the live Electron's `app.getPath('userData')`, not
 * Python's `default_app_data_dir()` — Plan 0007 phase 1 made Electron pass
 * `MARKET_ANALYSER_DATA_DIR=<userData>` to the spawned sidecar, and under
 * `_electron.launch` (unpackaged) those two paths diverge
 * (`<Roaming>/Electron/` vs `<Roaming>/market-analyser/`).
 *
 * Returns the inserted annotation's id.
 */
function insertAnnotation(args: InsertAnnotationArgs, dataDir: string): string {
  const script = [
    'import json, sys',
    'from datetime import datetime',
    'from market_analyser.annotations.types import Annotation, AnnotationKind',
    'from market_analyser.config import load_config',
    'from market_analyser.persistence.annotations_repository import AnnotationsRepository',
    'from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory',
    'cfg = load_config(None)',
    'engine = make_engine(cfg.db_path)',
    'apply_migrations(engine)',
    'repo = AnnotationsRepository(make_session_factory(engine))',
    'raw = json.loads(sys.stdin.read())',
    'a = Annotation(symbol=raw["symbol"], timeframe=raw["timeframe"], event_ts=datetime.fromisoformat(raw["event_ts"]), kind=AnnotationKind(raw["kind"]), label=raw.get("label"), agent_id="e2e")',
    'repo.insert(a)',
    'print(a.id)',
  ].join('; ')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    input: JSON.stringify(args),
    encoding: 'utf-8',
    shell: false,
    env: { ...process.env, MARKET_ANALYSER_DATA_DIR: dataDir },
  })
  if (result.status !== 0) {
    throw new Error(`insertAnnotation failed (exit ${result.status}): ${result.stderr}`)
  }
  return result.stdout.trim()
}

async function getDataDir(app: ElectronApplication): Promise<string> {
  return app.evaluate(({ app: electronApp }) => electronApp.getPath('userData'))
}

/**
 * Latest `end` (ms) among the captured /ohlcv request URLs at or after
 * `fromIndex`, or -Infinity if none carry a parseable `end`.
 *
 * The chart's lazy-history loader (Plan 0030) fires its own /ohlcv requests on
 * mount, reaching BACKWARD to older windows (an EARLIER `end`). So the Refresh
 * fetch can't be identified by request index — it's identified by its window end
 * advancing to ~now. Taking the max end over post-click requests ignores any
 * interleaved lazy-history reads (whose ends stay below the initial load's).
 */
function latestEndMs(urls: string[], fromIndex: number): number {
  const ends = urls
    .slice(fromIndex)
    .map((u) => new URL(u).searchParams.get('end'))
    .filter((e): e is string => e !== null)
    .map((e) => new Date(e).getTime())
    .filter((n) => !Number.isNaN(n))
  return ends.length > 0 ? Math.max(...ends) : Number.NEGATIVE_INFINITY
}

test('cold launch renders a candlestick chart for the default symbol', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // First prove OhlcvView mounted — independent of whether the sidecar fetch
  // succeeds. The section has a stable aria-label.
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Then wait for the chart canvas OR a visible error state OR an empty
  // state. All three prove the useOhlcv hook ran end-to-end (success,
  // surfaced failure, or no-bars). A hang here would mean an infinite
  // spinner, which is the UX failure the four-state discipline exists to
  // prevent. The empty branch is a real user state (offline + uncached
  // symbol + bad range) -- Plan 0004 phase 7 added a role+testid so this
  // predicate can match it without re-routing it through the error state.
  const chart = window.locator('[data-testid="candlestick-chart"]')
  const errorState = window.getByRole('alert')
  const emptyState = window.locator('[data-testid="ohlcv-empty"]')
  await expect(async () => {
    const chartVisible = await chart.isVisible().catch(() => false)
    const errorVisible = await errorState.isVisible().catch(() => false)
    const emptyVisible = await emptyState.isVisible().catch(() => false)
    expect(chartVisible || errorVisible || emptyVisible).toBe(true)
  }).toPass({ timeout: 30_000 })

  if (await chart.isVisible()) {
    const canvasCount = await chart.locator('canvas').count()
    expect(canvasCount).toBeGreaterThan(0)
  }

  await app.close()
})

test('Refresh advances the OHLCV window end timestamp', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()

  // Attach the request listener BEFORE awaiting load — the initial /ohlcv
  // fetch fires as soon as OhlcvView mounts, and we need to capture it.
  const ohlcvUrls: string[] = []
  window.on('request', (req) => {
    const url = req.url()
    if (/^http:\/\/127\.0\.0\.1:\d+\/ohlcv\?/.test(url)) {
      ohlcvUrls.push(url)
    }
  })

  await window.waitForLoadState('domcontentloaded')
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Wait for the initial fetch to land before clicking Refresh.
  await expect.poll(() => ohlcvUrls.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  const firstEnd = new URL(ohlcvUrls[0]).searchParams.get('end')
  expect(firstEnd).not.toBeNull()
  const firstEndMs = new Date(firstEnd!).getTime()

  // Snapshot the request count before the click. The next request by index is
  // NOT guaranteed to be the Refresh fetch — the lazy-history loader (Plan 0030)
  // interleaves its own /ohlcv reads with an earlier `end` (see latestEndMs).
  const beforeRefresh = ohlcvUrls.length

  // Tiny pause so Date.now() definitely differs between the two memo computes.
  await window.waitForTimeout(50)

  const refreshButton = window.getByRole('button', { name: 'Refresh' })
  await expect(refreshButton).toBeEnabled({ timeout: 15_000 })
  await refreshButton.click()

  // Refresh advances range_end to ~now, so its /ohlcv request carries an `end`
  // strictly later than the initial load's. Wait for a post-click request whose
  // end advanced past firstEnd, ignoring any interleaved lazy-history reads.
  await expect
    .poll(() => latestEndMs(ohlcvUrls, beforeRefresh), { timeout: 15_000 })
    .toBeGreaterThan(firstEndMs)

  await app.close()
})

test('annotation written to the DB surfaces on the renderer within a poll window', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

  // Wait for the chart view to mount so the poll loop is alive.
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Capture annotation responses so we can assert the renderer received our row.
  const annotationsResponses: Array<{ url: string; body: string }> = []
  window.on('response', async (response) => {
    const url = response.url()
    if (!/127\.0\.0\.1:\d+\/annotations\?/.test(url)) return
    try {
      const body = await response.text()
      annotationsResponses.push({ url, body })
    } catch {
      // ignore — response may already be discarded
    }
  })

  // Insert a bullish annotation at a recent date inside the default 365-day
  // window. Mid-yesterday-UTC keeps it inside the OhlcvView's lookback.
  const eventDate = new Date(Date.now() - 24 * 60 * 60 * 1000)
  eventDate.setUTCHours(12, 0, 0, 0)
  const expectedLabel = `e2e-marker-${Date.now()}`
  const annotationId = insertAnnotation(
    {
      symbol: 'AAPL',
      timeframe: '1d',
      event_ts: eventDate.toISOString(),
      kind: 'bullish_marker',
      label: expectedLabel,
    },
    dataDir,
  )
  expect(annotationId).toMatch(/^[0-9a-f]{32}$/)

  // Within ~2 poll cycles, the renderer's GET /annotations should include
  // the inserted row.
  await expect
    .poll(() => annotationsResponses.some((r) => r.body.includes(annotationId)), {
      timeout: 5_000,
      intervals: [200],
    })
    .toBe(true)

  // Cross-check: the matching response also carries the label verbatim, so
  // the marker rendered on the chart would have the right tooltip text.
  const matched = annotationsResponses.find((r) => r.body.includes(annotationId))
  expect(matched).toBeDefined()
  expect(matched!.body).toContain(expectedLabel)

  // And the chart canvas is still present (marker rendering layers on, doesn't
  // recreate the chart).
  await expect(window.locator('[data-testid="candlestick-chart"]')).toBeVisible()

  await app.close()
})
