/**
 * Plan 0027 phase 3: the chart's volume math is pure and lookahead-free.
 *
 * `computeVolumeBars` / `computeVolumeMa` / `computeVwap` / `computeObv` are the
 * renderer's volume-pane helpers. Correctness is pinned against hand-computed
 * values; the lookahead-bias rule (project-wide) requires that the value at bar
 * `i` depends only on `bars[0..=i]`, defended with the same "truncation
 * invariant" test `lib/indicators.test.ts` uses.
 */
import type { LineData } from 'lightweight-charts'

import {
  VOLUME_BEARISH_COLOR,
  VOLUME_BULLISH_COLOR,
  computeAccumulationDistribution,
  computeChaikinMoneyFlow,
  computeMfi,
  computeObv,
  computeVolumeBars,
  computeVolumeMa,
  computeVwap,
} from './volume'
import type { Bar } from '../types/sidecar/bar'

function bar(
  eventTs: string,
  opts: { open: number; high: number; low: number; close: number; volume: number },
): Bar {
  return {
    symbol: 'TEST',
    timeframe: '1d',
    event_ts: eventTs,
    open: opts.open,
    high: opts.high,
    low: opts.low,
    close: opts.close,
    volume: opts.volume,
    source: 'test',
  }
}

function day(i: number): string {
  const d = new Date('2026-04-01T00:00:00+00:00')
  d.setUTCDate(d.getUTCDate() + i)
  return d.toISOString()
}

/** Flat OHLC at `close`, controllable volume — for the MA/OBV/truncation tests. */
function flatBar(i: number, close: number, volume: number): Bar {
  return bar(day(i), { open: close, high: close, low: close, close, volume })
}

describe('computeVolumeBars', () => {
  it('tints each bar bullish when close >= open and bearish otherwise', () => {
    const bars: Bar[] = [
      bar(day(0), { open: 10, high: 11, low: 9, close: 11, volume: 100 }), // up
      bar(day(1), { open: 11, high: 12, low: 8, close: 9, volume: 200 }), // down
      bar(day(2), { open: 12, high: 12, low: 12, close: 12, volume: 50 }), // flat -> bullish (>=)
    ]
    const out = computeVolumeBars(bars)
    expect(out).toHaveLength(3)
    expect(out[0].color).toBe(VOLUME_BULLISH_COLOR)
    expect(out[0].value).toBe(100)
    expect(out[1].color).toBe(VOLUME_BEARISH_COLOR)
    expect(out[2].color).toBe(VOLUME_BULLISH_COLOR) // close == open counts as bullish
  })

  it('returns an empty array for empty bars', () => {
    expect(computeVolumeBars([])).toEqual([])
  })
})

describe('computeVolumeMa', () => {
  it('produces correct trailing MA(3) values over a known volume series', () => {
    const volumes = [100, 200, 300, 400, 500]
    const bars = volumes.map((v, i) => flatBar(i, 10, v))
    // MA(3): (100+200+300)/3=200, (200+300+400)/3=300, (300+400+500)/3=400
    expect(computeVolumeMa(bars, 3).map((p) => p.value)).toEqual([200, 300, 400])
  })

  it('returns N - period + 1 points starting at the (period-1)th bar', () => {
    const bars = [100, 200, 300, 400, 500].map((v, i) => flatBar(i, 10, v))
    const ma = computeVolumeMa(bars, 3)
    expect(ma).toHaveLength(bars.length - 3 + 1)
    expect(ma[0].time).toBe(Math.floor(new Date(bars[2].event_ts).getTime() / 1000))
  })

  it('returns empty when period > bars.length or period <= 0', () => {
    const bars = [100, 200].map((v, i) => flatBar(i, 10, v))
    expect(computeVolumeMa(bars, 5)).toEqual([])
    expect(computeVolumeMa(bars, 0)).toEqual([])
  })

  it('is lookahead-free: MA at bar k matches whether fed bars[0..=k] or the full series', () => {
    const bars = [100, 200, 300, 400, 500, 600, 700].map((v, i) => flatBar(i, 10, v))
    const full = computeVolumeMa(bars, 3)
    for (let k = 2; k < bars.length; k++) {
      const truncated = computeVolumeMa(bars.slice(0, k + 1), 3)
      expect(truncated[truncated.length - 1].value).toBeCloseTo(full[k - 2].value)
    }
  })
})

describe('computeVwap', () => {
  it('computes rolling trailing VWAP(2) of the typical price by hand', () => {
    const bars: Bar[] = [
      bar(day(0), { open: 9, high: 10, low: 8, close: 9, volume: 100 }), // tp = 9
      bar(day(1), { open: 11, high: 12, low: 10, close: 11, volume: 300 }), // tp = 11
      bar(day(2), { open: 13, high: 14, low: 12, close: 13, volume: 100 }), // tp = 13
    ]
    const vwap = computeVwap(bars, 2)
    expect(vwap).toHaveLength(2) // starts at index period-1 = 1
    // i=1: (9*100 + 11*300) / 400 = 4200/400 = 10.5
    expect(vwap[0].value).toBeCloseTo(10.5)
    // i=2: (11*300 + 13*100) / 400 = 4600/400 = 11.5
    expect(vwap[1].value).toBeCloseTo(11.5)
  })

  it('skips a zero-volume window rather than emitting NaN/Infinity', () => {
    const bars = Array.from({ length: 4 }, (_, i) =>
      bar(day(i), { open: 10, high: 11, low: 9, close: 10, volume: 0 }),
    )
    const vwap = computeVwap(bars, 2)
    expect(vwap).toEqual([])
    expect(vwap.every((p) => Number.isFinite(p.value))).toBe(true)
  })

  it('returns empty when period > bars.length or period <= 0', () => {
    const bars = [
      bar(day(0), { open: 9, high: 10, low: 8, close: 9, volume: 100 }),
      bar(day(1), { open: 11, high: 12, low: 10, close: 11, volume: 300 }),
    ]
    expect(computeVwap(bars, 5)).toEqual([])
    expect(computeVwap(bars, 0)).toEqual([])
  })

  it('is lookahead-free: VWAP at bar k matches whether fed bars[0..=k] or the full series', () => {
    const bars = Array.from({ length: 7 }, (_, i) =>
      bar(day(i), { open: 10 + i, high: 12 + i, low: 8 + i, close: 10 + i, volume: 100 + 10 * i }),
    )
    const full = computeVwap(bars, 2)
    for (let k = 1; k < bars.length; k++) {
      const truncated = computeVwap(bars.slice(0, k + 1), 2)
      expect(truncated[truncated.length - 1].value).toBeCloseTo(full[k - 1].value)
    }
  })
})

describe('computeObv', () => {
  it('accumulates by close direction, seeded at 0', () => {
    const closes = [10, 11, 10, 12, 12, 13]
    const volumes = [100, 200, 150, 300, 50, 250]
    const bars = closes.map((c, i) => flatBar(i, c, volumes[i]))
    // seed 0; +200 (up); -150 (down); +300 (up); +0 (flat); +250 (up)
    expect(computeObv(bars).map((p) => p.value)).toEqual([0, 200, 50, 350, 350, 600])
  })

  it('returns an empty array for empty bars', () => {
    expect(computeObv([])).toEqual([])
  })

  it('is lookahead-free: OBV at bar k matches whether fed bars[0..=k] or the full series', () => {
    const closes = [10, 11, 10, 12, 12, 13, 12]
    const bars = closes.map((c, i) => flatBar(i, c, 100))
    const full = computeObv(bars)
    for (let k = 0; k < bars.length; k++) {
      const truncated = computeObv(bars.slice(0, k + 1))
      expect(truncated[truncated.length - 1].value).toBe(full[k].value)
    }
  })
})

// ----------------------------------------------------------------------------
// Money-flow indicators (Plan 0091 phase 7) — pinned to analysis/volume.py.
// Expected values generated by mfi(bars,3) / chaikin_money_flow(bars,3) /
// accumulation_distribution(bars) over the identical OHLCV fixture, within 1e-6.
// ----------------------------------------------------------------------------

// (high, low, close, volume) — open == close; the same fixture the Python
// reference values below were generated from.
const MF_OHLCV: Array<[number, number, number, number]> = [
  [11, 9, 10, 100],
  [13, 10, 12, 200],
  [14, 11, 11, 150],
  [12, 9, 10, 300],
  [16, 11, 15, 250],
  [17, 14, 16, 120],
  [15, 12, 13, 400],
  [14, 10, 11, 180],
  [18, 13, 17, 220],
  [19, 15, 18, 160],
]
const MF_FIXTURE: Bar[] = MF_OHLCV.map(([high, low, close, volume], i) =>
  bar(day(i), { open: close, high, low, close, volume }),
)

function mfTimeAt(i: number): number {
  return Math.floor(new Date(MF_FIXTURE[i].event_ts).getTime() / 1000)
}

function expectMfSeries(series: LineData[], expected: Array<[number, number]>): void {
  expect(series).toHaveLength(expected.length)
  expected.forEach(([i, value], idx) => {
    expect(series[idx].time).toBe(mfTimeAt(i))
    expect(series[idx].value).toBeCloseTo(value, 6)
  })
}

describe('computeMfi (pinned to analysis.volume.mfi)', () => {
  it('matches within 1e-6 (period=3)', () => {
    expectMfSeries(computeMfi(MF_FIXTURE, 3), [
      [3, 57.1428571429],
      [4, 63.0952380952],
      [5, 63.4433962264],
      [6, 50.2177971375],
      [7, 20.1861130995],
      [8, 32.1363359708],
      [9, 74.9801429706],
    ])
  })

  it('returns empty for too few bars / invalid period', () => {
    expect(computeMfi(MF_FIXTURE.slice(0, 3), 3)).toEqual([])
    expect(computeMfi(MF_FIXTURE, 0)).toEqual([])
  })

  it('is lookahead-free', () => {
    const full = computeMfi(MF_FIXTURE, 3)
    for (let end = 4; end <= MF_FIXTURE.length; end++) {
      const trunc = computeMfi(MF_FIXTURE.slice(0, end), 3)
      const last = trunc[trunc.length - 1]
      expect(last.value).toBeCloseTo(full.find((p) => p.time === last.time)!.value, 6)
    }
  })
})

describe('computeChaikinMoneyFlow (pinned to analysis.volume.chaikin_money_flow)', () => {
  it('matches within 1e-6 (period=3)', () => {
    expectMfSeries(computeChaikinMoneyFlow(MF_FIXTURE, 3), [
      [2, -0.1851851852],
      [3, -0.2820512821],
      [4, -0.1428571429],
      [5, 0.1343283582],
      [6, 0.0735930736],
      [7, -0.2619047619],
      [8, -0.1141666667],
      [9, 0.2178571429],
    ])
  })

  it('is lookahead-free', () => {
    const full = computeChaikinMoneyFlow(MF_FIXTURE, 3)
    for (let end = 3; end <= MF_FIXTURE.length; end++) {
      const trunc = computeChaikinMoneyFlow(MF_FIXTURE.slice(0, end), 3)
      const last = trunc[trunc.length - 1]
      expect(last.value).toBeCloseTo(full.find((p) => p.time === last.time)!.value, 6)
    }
  })
})

describe('computeAccumulationDistribution (pinned to analysis.volume.accumulation_distribution)', () => {
  it('matches within 1e-6, dense from bar 0', () => {
    expectMfSeries(computeAccumulationDistribution(MF_FIXTURE), [
      [0, 0.0],
      [1, 66.6666666667],
      [2, -83.3333333333],
      [3, -183.3333333333],
      [4, -33.3333333333],
      [5, 6.6666666667],
      [6, -126.6666666667],
      [7, -216.6666666667],
      [8, -84.6666666667],
      [9, -4.6666666667],
    ])
  })

  it('is lookahead-free (cumulative — value at k fixed once bars[0..=k] exist)', () => {
    const full = computeAccumulationDistribution(MF_FIXTURE)
    for (let end = 1; end <= MF_FIXTURE.length; end++) {
      const trunc = computeAccumulationDistribution(MF_FIXTURE.slice(0, end))
      expect(trunc[trunc.length - 1].value).toBeCloseTo(full[end - 1].value, 6)
    }
  })
})
