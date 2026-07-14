/**
 * Plan 0097 phase 2: the pure snap helpers behind the drawing edit engine
 * (`hooks/useDrawingHitTest.ts`). `nearestOhlc` picks the bar OHLC value closest
 * to a price; `snapAnchor` resolves a fractional bar-logical + price to a snapped
 * `(time, price)` anchor on a real bar — the done-when "snap resolves to an OHLC
 * value" claim. The chart-bound `useDrawingHitTest` hook is exercised end-to-end
 * in the phase-5 human smoke.
 */
import type { Bar } from '../types/sidecar/bar'
import { nearestOhlc, snapAnchor } from './useDrawingHitTest'

function bar(ts: string, o: number, h: number, l: number, c: number): Bar {
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: ts,
    open: o,
    high: h,
    low: l,
    close: c,
    volume: 0,
    source: 'test',
  }
}

const BARS: Bar[] = [
  bar('2026-05-01T00:00:00Z', 100, 110, 95, 105),
  bar('2026-05-02T00:00:00Z', 105, 120, 104, 118),
  bar('2026-05-03T00:00:00Z', 118, 125, 112, 115),
]

describe('nearestOhlc', () => {
  it('returns the OHLC value closest to the price', () => {
    const b = bar('t', 100, 110, 95, 105)
    expect(nearestOhlc(b, 109)).toBe(110) // nearest high
    expect(nearestOhlc(b, 96)).toBe(95) // nearest low
    expect(nearestOhlc(b, 101)).toBe(100) // nearest open
    expect(nearestOhlc(b, 104)).toBe(105) // nearest close
  })
})

describe('snapAnchor', () => {
  it('rounds the logical to a bar and snaps the price to that bar OHLC', () => {
    // logical 1.4 → bar 1 (2026-05-02); price 119 → nearest OHLC is 120 (its high).
    expect(snapAnchor(BARS, 1.4, 119)).toEqual({ ts: '2026-05-02T00:00:00Z', price: 120 })
  })

  it('clamps an out-of-range logical to the nearest bar', () => {
    expect(snapAnchor(BARS, -3, 100)).toEqual({ ts: '2026-05-01T00:00:00Z', price: 100 })
    expect(snapAnchor(BARS, 99, 113)).toEqual({ ts: '2026-05-03T00:00:00Z', price: 112 })
  })

  it('returns null when there are no bars to snap to', () => {
    expect(snapAnchor([], 0, 100)).toBeNull()
  })
})
