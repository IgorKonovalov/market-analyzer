/**
 * Plan 0049 phase 7: the pure span helpers in `lib/spans.ts`.
 *
 * `markersToSpans` selects only multi-bar markers (both endpoints present);
 * `computeSpanRects` maps spans to pixel rectangles via a time→x converter,
 * skipping off-screen endpoints and normalising x1 <= x2 — canvas-free, so the
 * coordinate logic is tested without a real chart.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import type { ChartMarker, MarkerColors } from './markers'
import { computeSpanRects, markersToSpans, type PatternSpan } from './spans'

const COLORS: MarkerColors = { bullish: '#00ff00', bearish: '#ff0000', neutral: '#888888' }

const toUtc = (iso: string): UTCTimestamp =>
  Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp

const START = '2026-05-13T00:00:00Z'
const END = '2026-05-15T00:00:00Z'
const SPAN: PatternSpan = {
  startTs: START,
  endTs: END,
  kind: 'bullish_marker',
  pattern: 'morning_star',
}

describe('markersToSpans', () => {
  it('includes only markers carrying BOTH span endpoints (multi-bar)', () => {
    const markers: ChartMarker[] = [
      {
        event_ts: END,
        kind: 'bullish_marker',
        pattern: 'morning_star',
        span_start_ts: START,
        span_end_ts: END,
      },
      // single-bar doji — no span endpoints → excluded
      { event_ts: END, kind: 'neutral_marker', pattern: 'doji' },
    ]
    const spans = markersToSpans(markers)
    expect(spans).toHaveLength(1)
    expect(spans[0]).toMatchObject({ pattern: 'morning_star', startTs: START, endTs: END })
  })

  it('returns [] when no marker has a span', () => {
    expect(markersToSpans([{ event_ts: END, kind: 'bullish_marker', pattern: 'hammer' }])).toEqual(
      [],
    )
  })
})

describe('computeSpanRects', () => {
  it('maps a 3-bar span to one rect (x1<x2) via the time→x converter', () => {
    const timeToX = (t: UTCTimestamp): number | null =>
      t === toUtc(START) ? 100 : t === toUtc(END) ? 160 : null
    const [rect] = computeSpanRects([SPAN], timeToX, COLORS)
    expect(rect.x1).toBe(100)
    expect(rect.x2).toBe(160)
    // Colour derives from the bullish token (translucent), not a hardcoded hex.
    expect(rect.color.startsWith('#00ff00')).toBe(true)
  })

  it('colours a bearish span from the bearish token', () => {
    const bearish: PatternSpan = { ...SPAN, kind: 'bearish_marker' }
    const [rect] = computeSpanRects([bearish], () => 10, COLORS)
    expect(rect.color.startsWith('#ff0000')).toBe(true)
  })

  it('skips a span whose endpoint maps off-screen (converter returns null)', () => {
    expect(computeSpanRects([SPAN], () => null, COLORS)).toEqual([])
  })

  it('normalises to x1 <= x2 even if the converter returns them reversed', () => {
    const timeToX = (t: UTCTimestamp): number | null => (t === toUtc(START) ? 200 : 50)
    const [rect] = computeSpanRects([SPAN], timeToX, COLORS)
    expect(rect.x1).toBe(50)
    expect(rect.x2).toBe(200)
  })
})
