/**
 * Plan 0013 phase 4 done-when: useBackfillState behavior.
 *
 * Drives the hook through `backfillBus` (the same seam `App.tsx`'s
 * `useEventStream` handlers feed) and asserts:
 * - started (matching key) → isBackfilling true
 * - backfilled (matching key) → isBackfilling false AND refetch called once
 * - failed (matching key) → isBackfilling false, refetch NOT called, error set
 * - cross-symbol isolation: a non-matching key is ignored
 * - out-of-order: backfilled before started still refetches; ends not-backfilling
 */
import '@testing-library/jest-dom'

import { act, renderHook } from '@testing-library/react'

import { notifyBackfill } from '../handlers/backfillBus'
import { useBackfillState } from './useBackfillState'

const SYMBOL = 'AAPL'
const TF = '1d'

function startedEvent(symbol = SYMBOL, timeframe = TF): void {
  notifyBackfill({
    kind: 'started',
    payload: {
      symbol,
      timeframe,
      gaps: [{ start: '2026-04-01T00:00:00Z', end: '2026-05-01T00:00:00Z' }],
    },
  })
}

function backfilledEvent(symbol = SYMBOL, timeframe = TF): void {
  notifyBackfill({
    kind: 'backfilled',
    payload: {
      symbol,
      timeframe,
      range_start: '2026-04-01T00:00:00Z',
      range_end: '2026-05-01T00:00:00Z',
      bars_added: 21,
    },
  })
}

function failedEvent(symbol = SYMBOL, timeframe = TF): void {
  notifyBackfill({
    kind: 'failed',
    payload: {
      symbol,
      timeframe,
      reason: 'rate_limited',
      message: 'yahoo: rate limited (HTTP 429)',
    },
  })
}

it('flips isBackfilling true on a matching started event', () => {
  const refetch = jest.fn()
  const { result } = renderHook(() => useBackfillState({ symbol: SYMBOL, timeframe: TF, refetch }))

  expect(result.current.isBackfilling).toBe(false)
  act(() => startedEvent())
  expect(result.current.isBackfilling).toBe(true)
  expect(result.current.error).toBeNull()
})

it('on backfilled: clears isBackfilling and calls refetch exactly once', () => {
  const refetch = jest.fn()
  const { result } = renderHook(() => useBackfillState({ symbol: SYMBOL, timeframe: TF, refetch }))

  act(() => startedEvent())
  act(() => backfilledEvent())

  expect(result.current.isBackfilling).toBe(false)
  expect(refetch).toHaveBeenCalledTimes(1)
})

it('on failed: clears isBackfilling, sets error, does NOT refetch', () => {
  const refetch = jest.fn()
  const { result } = renderHook(() => useBackfillState({ symbol: SYMBOL, timeframe: TF, refetch }))

  act(() => startedEvent())
  act(() => failedEvent())

  expect(result.current.isBackfilling).toBe(false)
  expect(refetch).not.toHaveBeenCalled()
  expect(result.current.error).toEqual({
    reason: 'rate_limited',
    message: 'yahoo: rate limited (HTTP 429)',
  })
})

it('ignores events for a different (symbol, timeframe)', () => {
  const refetch = jest.fn()
  const { result } = renderHook(() => useBackfillState({ symbol: SYMBOL, timeframe: TF, refetch }))

  act(() => startedEvent('MSFT', TF))
  expect(result.current.isBackfilling).toBe(false)

  act(() => backfilledEvent('MSFT', TF))
  expect(refetch).not.toHaveBeenCalled()
})

it('out-of-order: backfilled before started still refetches and ends not-backfilling', () => {
  const refetch = jest.fn()
  const { result } = renderHook(() => useBackfillState({ symbol: SYMBOL, timeframe: TF, refetch }))

  act(() => backfilledEvent())

  expect(refetch).toHaveBeenCalledTimes(1)
  expect(result.current.isBackfilling).toBe(false)
})
