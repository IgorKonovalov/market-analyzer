/**
 * Plan 0007 phase 4.5: indicator math is pure and lookahead-free.
 *
 * `computeEma` and `computeSma` are the renderer's overlay helpers. The
 * lookahead-bias rule (project-wide) requires that value at bar i depends
 * only on bars[0..=i]; we defend that with a "truncation invariant" test:
 * computing the indicator over `bars[0..=k]` must produce the same value
 * at index k as computing it over the full series.
 */
import { computeEma, computeSma } from './indicators'
import type { Bar } from '../types/sidecar/bar'

function bar(eventTs: string, close: number): Bar {
  return {
    symbol: 'TEST',
    timeframe: '1d',
    event_ts: eventTs,
    open: close,
    high: close,
    low: close,
    close,
    volume: 0,
    source: 'test',
  }
}

const FIXTURE: Bar[] = [
  bar('2026-04-01T00:00:00+00:00', 10),
  bar('2026-04-02T00:00:00+00:00', 11),
  bar('2026-04-03T00:00:00+00:00', 12),
  bar('2026-04-04T00:00:00+00:00', 13),
  bar('2026-04-05T00:00:00+00:00', 14),
  bar('2026-04-06T00:00:00+00:00', 15),
  bar('2026-04-07T00:00:00+00:00', 16),
  bar('2026-04-08T00:00:00+00:00', 17),
]

describe('computeSma', () => {
  it('returns N - period + 1 points', () => {
    expect(computeSma(FIXTURE, 3)).toHaveLength(FIXTURE.length - 3 + 1)
  })

  it('starts at the (period-1)th bar', () => {
    const sma = computeSma(FIXTURE, 3)
    const firstBarTs = Math.floor(new Date(FIXTURE[2].event_ts).getTime() / 1000)
    expect(sma[0].time).toBe(firstBarTs)
    // SMA(3) over closes [10, 11, 12] = 11
    expect(sma[0].value).toBeCloseTo(11)
  })

  it('produces correct values for SMA(3) over the fixture', () => {
    const sma = computeSma(FIXTURE, 3)
    // Closes: 10, 11, 12, 13, 14, 15, 16, 17
    // SMA(3) at each index >=2: 11, 12, 13, 14, 15, 16
    expect(sma.map((p) => p.value)).toEqual([11, 12, 13, 14, 15, 16])
  })

  it('returns empty if period > bars.length', () => {
    expect(computeSma(FIXTURE, 100)).toEqual([])
  })

  it('returns empty if period <= 0', () => {
    expect(computeSma(FIXTURE, 0)).toEqual([])
    expect(computeSma(FIXTURE, -5)).toEqual([])
  })

  it('is lookahead-free: value at bar k is the same whether we feed bars[0..=k] or the full series', () => {
    const full = computeSma(FIXTURE, 3)
    for (let k = 2; k < FIXTURE.length; k++) {
      const truncated = computeSma(FIXTURE.slice(0, k + 1), 3)
      const fullValueAtK = full[k - 2].value
      const truncatedLast = truncated[truncated.length - 1].value
      expect(truncatedLast).toBeCloseTo(fullValueAtK)
    }
  })
})

describe('computeEma', () => {
  it('seeds with the SMA of the first `period` closes', () => {
    const ema = computeEma(FIXTURE, 3)
    expect(ema[0].value).toBeCloseTo(11) // SMA of 10,11,12
  })

  it('returns N - period + 1 points', () => {
    expect(computeEma(FIXTURE, 3)).toHaveLength(FIXTURE.length - 3 + 1)
  })

  it('applies the standard EMA recurrence after the seed', () => {
    const ema = computeEma(FIXTURE, 3)
    // multiplier k = 2/(3+1) = 0.5
    // seed = 11; next close = 13; ema = (13 - 11) * 0.5 + 11 = 12
    expect(ema[1].value).toBeCloseTo(12)
    // next: close = 14; ema = (14 - 12) * 0.5 + 12 = 13
    expect(ema[2].value).toBeCloseTo(13)
  })

  it('returns empty if period > bars.length', () => {
    expect(computeEma(FIXTURE, 100)).toEqual([])
  })

  it('returns empty if period <= 0', () => {
    expect(computeEma(FIXTURE, 0)).toEqual([])
  })

  it('is lookahead-free: value at bar k is the same whether we feed bars[0..=k] or the full series', () => {
    const full = computeEma(FIXTURE, 3)
    for (let k = 2; k < FIXTURE.length; k++) {
      const truncated = computeEma(FIXTURE.slice(0, k + 1), 3)
      const fullValueAtK = full[k - 2].value
      const truncatedLast = truncated[truncated.length - 1].value
      expect(truncatedLast).toBeCloseTo(fullValueAtK)
    }
  })
})
