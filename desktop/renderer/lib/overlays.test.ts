/**
 * Plan 0029 phase 2: direct unit test for the overlay registry. Defends:
 *   - `computeOverlayData` for ema/sma with hand-computed line values,
 *   - `[]` for an unregistered kind or a missing period,
 *   - `overlayColorFor` + `isSupportedOverlay` read from the registry,
 *   - the seam is single-source: adding ONE registry entry makes a previously
 *     unsupported kind supported, colored, and computable in one step.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import {
  OVERLAY_REGISTRY,
  computeOverlayData,
  isSupportedOverlay,
  overlayColorFor,
} from './overlays'
import type { Bar } from '../types/sidecar/bar'

function bar(eventTs: string, close: number): Bar {
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: eventTs,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000,
    source: 'test',
  }
}

// Four bars, closes [1, 2, 3, 10] — chosen so EMA and SMA diverge on the last
// point, so a kind→math mix-up can't pass.
const BARS: Bar[] = [
  bar('2026-04-01T00:00:00+00:00', 1),
  bar('2026-04-02T00:00:00+00:00', 2),
  bar('2026-04-03T00:00:00+00:00', 3),
  bar('2026-04-04T00:00:00+00:00', 10),
]
const T2 = Date.UTC(2026, 3, 3) / 1000
const T3 = Date.UTC(2026, 3, 4) / 1000

describe('computeOverlayData', () => {
  it('computes SMA(3): trailing mean over [1,2,3]=2 then [2,3,10]=5', () => {
    expect(computeOverlayData(BARS, { kind: 'sma', period: 3 })).toEqual([
      { time: T2, value: 2 },
      { time: T3, value: 5 },
    ])
  })

  it('computes EMA(3): seed=SMA(2)=2, then (10-2)*0.5+2=6', () => {
    expect(computeOverlayData(BARS, { kind: 'ema', period: 3 })).toEqual([
      { time: T2, value: 2 },
      { time: T3, value: 6 },
    ])
  })

  it('returns [] for an unregistered kind (rsi)', () => {
    expect(computeOverlayData(BARS, { kind: 'rsi', period: 14 })).toEqual([])
  })

  it.each([{ period: null }, { period: undefined }])(
    'returns [] when period is $period',
    ({ period }) => {
      expect(computeOverlayData(BARS, { kind: 'ema', period })).toEqual([])
    },
  )
})

describe('overlayColorFor', () => {
  it.each([
    { kind: 'ema' as const, color: '#2563eb' },
    { kind: 'sma' as const, color: '#f97316' },
  ])('returns the registry color for $kind', ({ kind, color }) => {
    expect(overlayColorFor({ kind, period: 20 })).toBe(color)
  })

  it('falls back to neutral grey for an unregistered kind', () => {
    expect(overlayColorFor({ kind: 'rsi', period: 14 })).toBe('#888888')
  })
})

describe('isSupportedOverlay', () => {
  it.each([
    { kind: 'ema' as const, supported: true },
    { kind: 'sma' as const, supported: true },
    { kind: 'rsi' as const, supported: false },
    { kind: 'macd' as const, supported: false },
    { kind: 'bbands' as const, supported: false },
  ])('$kind → $supported', ({ kind, supported }) => {
    expect(isSupportedOverlay(kind)).toBe(supported)
  })
})

describe('the registry is the single seam for a new kind', () => {
  afterEach(() => {
    delete OVERLAY_REGISTRY.rsi
  })

  it('one entry makes a kind supported, colored, and computable at once', () => {
    expect(isSupportedOverlay('rsi')).toBe(false)

    OVERLAY_REGISTRY.rsi = {
      color: '#abcdef',
      compute: () => [{ time: T2 as UTCTimestamp, value: 42 }],
    }

    expect(isSupportedOverlay('rsi')).toBe(true)
    expect(overlayColorFor({ kind: 'rsi', period: 14 })).toBe('#abcdef')
    expect(computeOverlayData(BARS, { kind: 'rsi', period: 14 })).toEqual([{ time: T2, value: 42 }])
  })
})
