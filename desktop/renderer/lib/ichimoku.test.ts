/**
 * Plan 0073 phase 4 (ADR-0067): unit tests for the Ichimoku overlay math +
 * geometry. Defends:
 *   - `computeIchimoku` against an independent trailing high/low-midpoint
 *     reference (the faithful mirror of the Python `ichimoku`),
 *   - the DISPLACED plotting mapping — a value computed at bar `i` plots at
 *     logical `i` (Tenkan/Kijun), `i + displacement` (Senkou), `i - displacement`
 *     (Chikou) — the load-bearing "future/past axis" geometry,
 *   - the cloud colour rule (bull where Senkou A > B, bear below) + the crossover
 *     split (the fill flips green↔red exactly at an A-B crossing),
 *   - custom periods change the geometry.
 * Canvas-free — the pixel mapping (logical/price → x/y) is the chart's; these pin
 * the logical-space math the primitive strokes.
 */
import {
  ICHIMOKU_DEFAULTS,
  cloudColorFor,
  computeCloudRegions,
  computeIchimoku,
  computeIchimokuGeometry,
  type IchimokuColors,
  type IchimokuCloudPoint,
} from './ichimoku'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

function bar(i: number, high: number, low: number, close: number): Bar {
  const ts = new Date(Date.UTC(2026, 0, 1) + i * 86_400_000).toISOString()
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: ts,
    open: close,
    high,
    low,
    close,
    volume: 1000,
    source: 'test',
  }
}

/** A deterministic zig-zag fixture: highs/lows/closes all distinct per bar so a
 * high/low mix-up can't pass, long enough to define Ichimoku on the classic 52
 * span. */
function fixture(n: number): Bar[] {
  const bars: Bar[] = []
  for (let i = 0; i < n; i += 1) {
    const base = 100 + Math.sin(i / 3) * 10 + i * 0.5
    bars.push(bar(i, base + 2, base - 2, base + 0.5))
  }
  return bars
}

/** Independent trailing high/low midpoint over `period` bars ending at `i`. */
function hlMid(bars: Bar[], i: number, period: number): number {
  let hi = -Infinity
  let lo = Infinity
  for (let j = i - period + 1; j <= i; j += 1) {
    hi = Math.max(hi, bars[j].high)
    lo = Math.min(lo, bars[j].low)
  }
  return (hi + lo) / 2
}

const SENTINEL_COLORS: IchimokuColors = {
  tenkan: 'TENKAN',
  kijun: 'KIJUN',
  chikou: 'CHIKOU',
  spanA: 'SPAN_A',
  spanB: 'SPAN_B',
  cloudBull: 'BULL',
  cloudBear: 'BEAR',
}

describe('computeIchimoku', () => {
  const bars = fixture(80)

  it('is undefined until the widest window and matches the trailing midpoints', () => {
    const series = computeIchimoku(bars) // classic 9 / 26 / 52
    const definedFrom = 52 - 1
    for (let i = 0; i < definedFrom; i += 1) expect(series[i]).toBeNull()
    for (const i of [definedFrom, 60, 79]) {
      const v = series[i]
      expect(v).not.toBeNull()
      const tenkan = hlMid(bars, i, 9)
      const kijun = hlMid(bars, i, 26)
      expect(v!.tenkan).toBeCloseTo(tenkan, 9)
      expect(v!.kijun).toBeCloseTo(kijun, 9)
      expect(v!.senkouA).toBeCloseTo((tenkan + kijun) / 2, 9)
      expect(v!.senkouB).toBeCloseTo(hlMid(bars, i, 52), 9)
      expect(v!.chikou).toBeCloseTo(bars[i].close, 9) // trailing close, no lag baked in
    }
  })

  it('returns [] for invalid periods', () => {
    expect(computeIchimoku(bars, 0)).toEqual([])
    expect(computeIchimoku(bars, 9, 0)).toEqual([])
    expect(computeIchimoku(bars, 9, 26, 0)).toEqual([])
  })
})

describe('computeIchimokuGeometry — displaced mapping', () => {
  const bars = fixture(80)
  const spec: OverlaySpec = { kind: 'ichimoku' }

  it('plots Senkou +displacement, Chikou -displacement, Tenkan/Kijun on the bar', () => {
    const geom = computeIchimokuGeometry(bars, spec)
    const disp = ICHIMOKU_DEFAULTS.displacement // 26
    const firstBar = 52 - 1 // first bar with a defined value

    // Tenkan/Kijun ride their own bar (logical i).
    expect(geom.tenkan[0].logical).toBe(firstBar)
    expect(geom.kijun[0].logical).toBe(firstBar)
    // Senkou A/B (and the cloud) project `displacement` bars into the FUTURE.
    expect(geom.spanA[0].logical).toBe(firstBar + disp)
    expect(geom.spanB[0].logical).toBe(firstBar + disp)
    expect(geom.cloud[0].logical).toBe(firstBar + disp)
    // Chikou lags `displacement` bars into the PAST.
    expect(geom.chikou[0].logical).toBe(firstBar - disp)

    // The last Senkou point projects past the last candle (index 79) into
    // previously-empty axis space.
    const lastSpanA = geom.spanA[geom.spanA.length - 1]
    expect(lastSpanA.logical).toBe(79 + disp)

    // Values carry through from the computed series.
    const series = computeIchimoku(bars)
    expect(geom.spanA[0].value).toBeCloseTo(series[firstBar]!.senkouA, 9)
    expect(geom.chikou[0].value).toBeCloseTo(bars[firstBar].close, 9)
    expect(geom.cloud[0].a).toBeCloseTo(series[firstBar]!.senkouA, 9)
    expect(geom.cloud[0].b).toBeCloseTo(series[firstBar]!.senkouB, 9)
  })

  it('custom periods change the geometry (defined-from shifts to the widest window)', () => {
    const custom: OverlaySpec = {
      kind: 'ichimoku',
      conversion: 20,
      base: 60,
      span_b: 120,
      displacement: 30,
    }
    const bars130 = fixture(130)
    const geom = computeIchimokuGeometry(bars130, custom)
    // Widest window is span_b = 120 → first defined bar is index 119.
    expect(geom.tenkan[0].logical).toBe(119)
    expect(geom.spanA[0].logical).toBe(119 + 30)
    // Far fewer points than the classic defaults on the same bars.
    const classic = computeIchimokuGeometry(bars130, { kind: 'ichimoku' })
    expect(geom.tenkan.length).toBeLessThan(classic.tenkan.length)
  })
})

describe('cloud colour + crossover split', () => {
  it('cloudColorFor is bull when A > B, bear otherwise', () => {
    expect(cloudColorFor(10, 5, SENTINEL_COLORS)).toBe('BULL')
    expect(cloudColorFor(5, 10, SENTINEL_COLORS)).toBe('BEAR')
    expect(cloudColorFor(7, 7, SENTINEL_COLORS)).toBe('BEAR') // equal ⇒ not bull
  })

  it('a same-side interval is one trapezoid coloured by A-vs-B', () => {
    const cloud: IchimokuCloudPoint[] = [
      { logical: 0, a: 12, b: 6 },
      { logical: 1, a: 14, b: 7 },
    ]
    const regions = computeCloudRegions(cloud, SENTINEL_COLORS)
    expect(regions).toHaveLength(1)
    expect(regions[0].color).toBe('BULL')
    expect(regions[0].points).toHaveLength(4) // trapezoid
  })

  it('a crossover interval splits into two regions of opposite colour at the crossing', () => {
    // A starts below B (bear), ends above B (bull) → the fill flips at the cross.
    const cloud: IchimokuCloudPoint[] = [
      { logical: 0, a: 5, b: 10 },
      { logical: 1, a: 12, b: 6 },
    ]
    const regions = computeCloudRegions(cloud, SENTINEL_COLORS)
    expect(regions).toHaveLength(2)
    expect(regions[0].color).toBe('BEAR') // left of the cross: A < B
    expect(regions[1].color).toBe('BULL') // right of the cross: A > B
    // Both triangles meet at the same crossover vertex, strictly between the ends.
    const crossLeft = regions[0].points[1]
    const crossRight = regions[1].points[0]
    expect(crossLeft.logical).toBeCloseTo(crossRight.logical, 9)
    expect(crossLeft.value).toBeCloseTo(crossRight.value, 9)
    expect(crossLeft.logical).toBeGreaterThan(0)
    expect(crossLeft.logical).toBeLessThan(1)
    expect(regions[0].points).toHaveLength(3) // triangles
    expect(regions[1].points).toHaveLength(3)
  })
})
