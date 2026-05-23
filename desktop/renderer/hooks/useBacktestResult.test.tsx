/**
 * Plan 0008 phase 5 done-when for `useBacktestResult`. Six concrete claims:
 *   1. On mount with `enabled: true`, the hook registers a `run.completed`
 *      handler (asserted via `listenerCountForTests()`).
 *   2. On `run.completed v1` with `payload.kind === 'backtest'`, the hook
 *      calls `getBacktest(run_id)` exactly once.
 *   3. On `run.completed v1` with `payload.kind === 'analysis'` or `'defi'`,
 *      the hook does NOT call `getBacktest`.
 *   4. State transitions: `{status: 'idle'}` → `{status: 'loading'}` →
 *      `{status: 'ready', result}`.
 *   5. On `getBacktest` rejecting, state → `{status: 'error', error}` and
 *      `error.message` contains the run_id.
 *   6. On unmount, the listener is unregistered.
 *
 * Path: co-located with the hook, matching `useEventStream.test.tsx`'s
 * reconciliation note (jest.config.ts's `roots` only covers `renderer/`).
 */
import { act, renderHook, waitFor } from '@testing-library/react'

import { getBacktest as getBacktestMock } from '../api/backtests'
import { listenerCountForTests, notifyRunCompleted } from '../handlers/runCompletedBus'
import type { BacktestResult } from '../types/sidecar/backtest-result'
import { useBacktestResult } from './useBacktestResult'

jest.mock('../api/backtests', () => ({
  getBacktest: jest.fn(),
}))

const getBacktest = getBacktestMock as jest.MockedFunction<typeof getBacktestMock>

function fixtureResult(overrides: Partial<BacktestResult> = {}): BacktestResult {
  return {
    run_id: 'run-fixture',
    engine_version: '0.1.0',
    strategy_id: 'rsi',
    strategy_version: '0.1.0',
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-04-20T00:00:00+00:00',
    range_end: '2026-05-20T00:00:00+00:00',
    bars_hash: 'hash',
    params: { period: 14 },
    costs: { commission_bps: 0, slippage_bps: 0 },
    initial_capital: 10_000,
    sizing: 'fixed_fraction',
    started_at: '2026-05-20T14:00:00+00:00',
    finished_at: '2026-05-20T14:00:01+00:00',
    trades: [],
    equity_curve: [{ ts: '2026-04-20T00:00:00+00:00', equity: 10_000 }],
    metrics: {
      total_return: 0,
      sharpe: 0,
      max_drawdown: 0,
      max_drawdown_duration_bars: 0,
      win_rate: 0,
      trade_count: 0,
      buy_and_hold_return: 0,
    },
    ...overrides,
  }
}

beforeEach(() => {
  getBacktest.mockReset()
})

describe('useBacktestResult — bus subscription lifecycle', () => {
  it('registers a run.completed handler on mount and unregisters on unmount', () => {
    const before = listenerCountForTests()
    const { unmount } = renderHook(() => useBacktestResult())

    expect(listenerCountForTests()).toBe(before + 1)

    unmount()
    expect(listenerCountForTests()).toBe(before)
  })

  it('does NOT register when enabled: false', () => {
    const before = listenerCountForTests()
    renderHook(() => useBacktestResult({ enabled: false }))
    expect(listenerCountForTests()).toBe(before)
  })
})

describe('useBacktestResult — payload filtering', () => {
  it('calls getBacktest with the run_id when payload.kind === "backtest"', async () => {
    getBacktest.mockResolvedValueOnce(fixtureResult({ run_id: 'backtest-run-42' }))
    renderHook(() => useBacktestResult())

    await act(async () => {
      notifyRunCompleted({
        kind: 'backtest',
        run_id: 'backtest-run-42',
        artifact_path: 'backtest-run-42',
      })
    })

    expect(getBacktest).toHaveBeenCalledTimes(1)
    expect(getBacktest).toHaveBeenCalledWith('backtest-run-42')
  })

  it('does NOT call getBacktest when payload.kind === "analysis"', async () => {
    renderHook(() => useBacktestResult())

    await act(async () => {
      notifyRunCompleted({
        kind: 'analysis',
        run_id: 'analysis-run-1',
        artifact_path: 'analysis-run-1',
      })
    })

    expect(getBacktest).not.toHaveBeenCalled()
  })

  it('does NOT call getBacktest when payload.kind === "defi"', async () => {
    renderHook(() => useBacktestResult())

    await act(async () => {
      notifyRunCompleted({
        kind: 'defi',
        run_id: 'defi-run-1',
        artifact_path: 'defi-run-1',
      })
    })

    expect(getBacktest).not.toHaveBeenCalled()
  })
})

describe('useBacktestResult — state transitions', () => {
  it('transitions idle → loading → ready when getBacktest resolves', async () => {
    let resolve!: (r: BacktestResult) => void
    getBacktest.mockReturnValueOnce(new Promise<BacktestResult>((r) => (resolve = r)))

    const { result } = renderHook(() => useBacktestResult())

    expect(result.current.status).toBe('idle')

    await act(async () => {
      notifyRunCompleted({ kind: 'backtest', run_id: 'r1', artifact_path: 'r1' })
    })

    expect(result.current.status).toBe('loading')

    const fixture = fixtureResult({ run_id: 'r1' })
    await act(async () => {
      resolve(fixture)
      // Yield to the microtask queue so React picks up the resolved promise.
      await Promise.resolve()
    })

    await waitFor(() => expect(result.current.status).toBe('ready'))
    if (result.current.status === 'ready') {
      expect(result.current.result.run_id).toBe('r1')
    }
  })

  it('transitions to error with a message containing the run_id when getBacktest rejects', async () => {
    getBacktest.mockRejectedValueOnce(new Error('sidecar 404: not found'))

    const { result } = renderHook(() => useBacktestResult())

    await act(async () => {
      notifyRunCompleted({ kind: 'backtest', run_id: 'run-bad', artifact_path: 'run-bad' })
    })

    await waitFor(() => expect(result.current.status).toBe('error'))
    if (result.current.status === 'error') {
      expect(result.current.error.message).toContain('run-bad')
      expect(result.current.error.message).toContain('not found')
    }
  })
})

describe('useBacktestResult — click-through (runId prop)', () => {
  it('fetches immediately when runId is provided', async () => {
    getBacktest.mockResolvedValueOnce(fixtureResult({ run_id: 'click-1' }))
    const { result } = renderHook(
      ({ runId }: { runId: string | null }) => useBacktestResult({ runId }),
      {
        initialProps: { runId: 'click-1' },
      },
    )

    await waitFor(() => expect(getBacktest).toHaveBeenCalledWith('click-1'))
    await waitFor(() => expect(result.current.status).toBe('ready'))
  })
})
