/**
 * Plan 0047 phase 8: the pure crosshair → tooltip mapping. Hand-computed epoch
 * times (epoch seconds = Date.UTC(...) / 1000), the marker/overlay/empty cases.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import type { Annotation } from '../types/sidecar/annotation'
import type { OverlaySpec } from '../types/events'
import { overlayLabel, tooltipAtTime, tooltipPosition } from './tooltip'

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
