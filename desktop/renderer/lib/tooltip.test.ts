/**
 * Plan 0047 phase 8: the pure crosshair → tooltip mapping. Hand-computed epoch
 * times (epoch seconds = Date.UTC(...) / 1000), the marker/overlay/empty cases.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import type { Annotation } from '../types/sidecar/annotation'
import type { OverlaySpec, TrendlineSpec } from '../types/events'
import { localize, term } from '../glossary/types'
import {
  levelTooltipText,
  nearestLevelAtY,
  overlayLabel,
  structureTooltipText,
  tooltipAtTime,
  tooltipPosition,
  trendlineTooltipText,
} from './tooltip'

function annotation(overrides: Partial<Annotation> = {}): Annotation {
  return {
    id: 'ann-1',
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: '2026-04-15T00:00:00+00:00',
    kind: 'bullish_marker',
    label: 'hammer at support',
    agent_id: 'test',
    created_at: '2026-04-15T01:00:00+00:00',
    ...overrides,
  }
}

const APR15 = (Date.UTC(2026, 3, 15) / 1000) as UTCTimestamp
const APR20 = (Date.UTC(2026, 3, 20) / 1000) as UTCTimestamp

describe('tooltipAtTime', () => {
  it('shows a marker label when the crosshair is on that bar', () => {
    const content = tooltipAtTime(APR15, [annotation()], [])
    expect(content?.markers).toEqual(['hammer at support'])
    expect(content?.overlays).toEqual([])
  })

  it('falls back to a direction word when the marker has no label', () => {
    expect(tooltipAtTime(APR15, [annotation({ label: null })], [])?.markers).toEqual(['Bullish'])
    expect(
      tooltipAtTime(APR15, [annotation({ label: null, kind: 'bearish_marker' })], [])?.markers,
    ).toEqual(['Bearish'])
  })

  it('prefers the candlestick pattern display name over the label (Plan 0071 follow-up)', () => {
    const content = tooltipAtTime(
      APR15,
      [
        {
          event_ts: '2026-04-15T00:00:00+00:00',
          kind: 'bullish_marker',
          pattern: 'bullish_engulfing',
          label: 'raw-label',
        },
      ],
      [],
    )
    expect(content?.markers).toEqual(['Bullish engulfing'])
  })

  it('adds the what-it-means line for a single hovered candlestick marker (Plan 0085)', () => {
    const content = tooltipAtTime(
      APR15,
      [
        {
          event_ts: '2026-04-15T00:00:00+00:00',
          kind: 'bullish_marker',
          pattern: 'bullish_engulfing',
        },
      ],
      [],
    )
    expect(content?.markers).toEqual(['Bullish engulfing'])
    expect(content?.markerMeaning).toBe(localize(term('bullish_engulfing')!.whatItMeans, 'en'))
  })

  it('shows names only (no meaning) when several markers coincide on one bar', () => {
    const content = tooltipAtTime(
      APR15,
      [
        {
          event_ts: '2026-04-15T00:00:00+00:00',
          kind: 'bullish_marker',
          pattern: 'bullish_engulfing',
        },
        { event_ts: '2026-04-15T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
      ],
      [],
    )
    expect(content?.markers).toEqual(['Bullish engulfing', 'Hammer'])
    expect(content?.markerMeaning).toBeUndefined()
  })

  it('omits the meaning for a marker whose pattern token has no glossary entry', () => {
    const content = tooltipAtTime(
      APR15,
      [
        {
          event_ts: '2026-04-15T00:00:00+00:00',
          kind: 'bullish_marker',
          pattern: 'mystery_pattern',
        },
      ],
      [],
    )
    expect(content?.markers).toEqual(['Mystery pattern'])
    expect(content?.markerMeaning).toBeUndefined()
  })

  it('includes overlay readings at the crosshair', () => {
    const content = tooltipAtTime(APR15, [], [{ label: 'EMA(20)', value: 101.5 }])
    expect(content?.overlays).toEqual([{ label: 'EMA(20)', value: 101.5 }])
  })

  it('returns null when the crosshair is off any marker and there are no overlays', () => {
    expect(tooltipAtTime(APR20, [annotation()], [])).toBeNull()
  })

  it('returns null when there is no crosshair time (pointer left the chart)', () => {
    expect(tooltipAtTime(undefined, [annotation()], [{ label: 'EMA(20)', value: 1 }])).toBeNull()
  })
})

describe('overlayLabel', () => {
  it('formats an indicator overlay with its period', () => {
    expect(overlayLabel({ kind: 'ema', period: 20 } as OverlaySpec)).toBe('EMA(20)')
    expect(overlayLabel({ kind: 'sma', period: 50 } as OverlaySpec)).toBe('SMA(50)')
  })

  it('omits the parenthetical when there is no period', () => {
    expect(overlayLabel({ kind: 'price_line', price: 100, label: 'R1' } as OverlaySpec)).toBe(
      'PRICE_LINE',
    )
  })
})

describe('trendlineTooltipText (Plan 0067 phase 2)', () => {
  const line = (overrides: Partial<TrendlineSpec>): TrendlineSpec => ({
    points: [
      { ts: '2026-04-13T00:00:00+00:00', price: 100 },
      { ts: '2026-04-15T00:00:00+00:00', price: 104 },
    ],
    style: 'solid',
    ...overrides,
  })

  it('names the pattern and marks a solid line confirmed', () => {
    expect(trendlineTooltipText(line({ pattern: 'rising_wedge', style: 'solid' }))).toBe(
      'Rising wedge — confirmed',
    )
  })

  it('marks a dashed line forming', () => {
    expect(trendlineTooltipText(line({ pattern: 'head_shoulders', style: 'dashed' }))).toBe(
      'Head & shoulders — forming',
    )
  })

  it('falls back to "Trendline" for an unknown/absent pattern', () => {
    expect(trendlineTooltipText(line({ pattern: null, style: 'solid' }))).toBe(
      'Trendline — confirmed',
    )
    expect(trendlineTooltipText(line({ pattern: 'mystery', style: 'dashed' }))).toBe(
      'Trendline — forming',
    )
  })
})

describe('tooltipPosition (edge-aware placement, Plan 0049 phase 13)', () => {
  const CONTAINER = { containerWidth: 600, containerHeight: 400 }
  const SIZE = { width: 200, height: 100 }

  it('places down-right of the crosshair away from the edges', () => {
    const { left, top } = tooltipPosition({ x: 50, y: 40, ...SIZE, ...CONTAINER }, 12)
    expect(left).toBe(62) // x + offset
    expect(top).toBe(52)
  })

  it('flips LEFT near the right edge so it stays on-screen', () => {
    const x = 580 // within the tooltip width of the right edge
    const { left } = tooltipPosition({ x, y: 40, ...SIZE, ...CONTAINER }, 12)
    expect(left).toBe(x - 12 - SIZE.width) // flipped to the left of the crosshair
    expect(left + SIZE.width).toBeLessThanOrEqual(CONTAINER.containerWidth)
  })

  it('flips UP near the bottom edge', () => {
    const y = 390
    const { top } = tooltipPosition({ x: 50, y, ...SIZE, ...CONTAINER }, 12)
    expect(top).toBe(y - 12 - SIZE.height)
    expect(top + SIZE.height).toBeLessThanOrEqual(CONTAINER.containerHeight)
  })

  it('clamps fully inside even when the crosshair hugs the corner', () => {
    const { left, top } = tooltipPosition({ x: 599, y: 399, ...SIZE, ...CONTAINER }, 12)
    expect(left).toBeGreaterThanOrEqual(0)
    expect(top).toBeGreaterThanOrEqual(0)
    expect(left + SIZE.width).toBeLessThanOrEqual(CONTAINER.containerWidth)
    expect(top + SIZE.height).toBeLessThanOrEqual(CONTAINER.containerHeight)
  })
})

// Plan 0105 phase 6 (ADR-0100 rule 3): the nearest-level-by-Y proximity lookup
// the pivot/fib hover reuses instead of per-level hit-test primitives.
describe('nearestLevelAtY', () => {
  const LEVELS = [
    { title: 'R1', price: 130 },
    { title: 'P', price: 110 },
    { title: 'S1', price: 80 },
  ]
  // A linear price->pixel map: y = 400 - price (higher price = higher on pane).
  const priceToY = (price: number): number | null => 400 - price

  it('returns the nearest level within the threshold', () => {
    // y=272 -> R1 at y=270 (2px away), P at y=290 (18px away).
    expect(nearestLevelAtY(272, LEVELS, priceToY)).toEqual({ title: 'R1', price: 130 })
  })

  it('returns null when no level is within the threshold', () => {
    // y=280 sits 10px from R1 and 10px from P - both beyond the 5px default.
    expect(nearestLevelAtY(280, LEVELS, priceToY)).toBeNull()
  })

  it('picks the closer of two levels inside the threshold', () => {
    const tight = [
      { title: 'Fib 0.5', price: 100 },
      { title: 'Fib 0.618', price: 96 },
    ]
    // y=301: Fib 0.5 at y=300 (1px), Fib 0.618 at y=304 (3px).
    expect(nearestLevelAtY(301, tight, priceToY, 5)?.title).toBe('Fib 0.5')
  })

  it('skips a level whose price maps off-scale (null)', () => {
    const partial = (price: number): number | null => (price === 130 ? null : 400 - price)
    // R1 would be nearest but maps off-scale; nothing else within 5px of y=271.
    expect(nearestLevelAtY(271, LEVELS, partial)).toBeNull()
  })

  it('honours a custom pixel threshold', () => {
    expect(nearestLevelAtY(280, LEVELS, priceToY, 12)?.title).toBe('R1')
  })
})

describe('levelTooltipText', () => {
  it('reads identity + price', () => {
    expect(levelTooltipText({ title: 'R1', price: 130 })).toBe('R1 · 130.00')
    expect(levelTooltipText({ title: 'Fib 0.618', price: 88.2 })).toBe('Fib 0.618 · 88.20')
  })
})

// Plan 0105 phase 7: hovered market-structure markers read glossary-backed
// content, the same dual-hat entries the legend uses.
describe('structureTooltipText', () => {
  it('resolves a pivot label to its glossary name + meaning', () => {
    const record = term('hh')!
    expect(structureTooltipText('HH')).toBe(
      `${localize(record.term, 'en')} — ${localize(record.whatItMeans, 'en')}`,
    )
  })

  it('resolves the event labels case-insensitively (BOS / CHoCH)', () => {
    const bos = term('bos')!
    const choch = term('choch')!
    expect(structureTooltipText('BOS')).toContain(localize(bos.term, 'en'))
    expect(structureTooltipText('CHoCH')).toContain(localize(choch.term, 'en'))
  })

  it('localizes via the per-field fallback', () => {
    const record = term('hh')!
    expect(structureTooltipText('HH', 'ru')).toContain(localize(record.term, 'ru'))
  })

  it('degrades to the bare label when the glossary has no entry', () => {
    expect(structureTooltipText('XX')).toBe('XX')
  })
})
