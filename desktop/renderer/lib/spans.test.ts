/**
 * Plan 0049 phase 7: the pure span helpers in `lib/spans.ts`.
 *
 * `markersToSpans` selects only multi-bar markers (both endpoints present);
 * `computeSpanRects` maps spans to pixel rectangles via a time→x converter,
 * skipping off-screen endpoints and normalising x1 <= x2 — canvas-free, so the
 * coordinate logic is tested without a real chart.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import type { SeriesAttachedParameter, Time } from 'lightweight-charts'

import type { ChartMarker, MarkerColors } from './markers'
import {
  PatternSpanPrimitive,
  computeSpanRects,
  markerHighlightSpan,
  markersToSpans,
  type PatternSpan,
} from './spans'

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

describe('markerHighlightSpan (Plan 0071 follow-up)', () => {
  it('returns null for no hovered markers', () => {
    expect(markerHighlightSpan([])).toBeNull()
  })

  it('uses a single-bar marker event_ts for both endpoints', () => {
    const m: ChartMarker = { event_ts: END, kind: 'neutral_marker', pattern: 'doji' }
    expect(markerHighlightSpan([m])).toEqual({
      startTs: END,
      endTs: END,
      kind: 'neutral_marker',
      pattern: 'doji',
    })
  })

  it('spans a multi-bar marker from its span endpoints', () => {
    const m: ChartMarker = {
      event_ts: END,
      kind: 'bullish_marker',
      pattern: 'morning_star',
      span_start_ts: START,
      span_end_ts: END,
    }
    expect(markerHighlightSpan([m])).toMatchObject({
      startTs: START,
      endTs: END,
      kind: 'bullish_marker',
    })
  })

  it('unions the bars of several hovered markers', () => {
    const single: ChartMarker = { event_ts: START, kind: 'bullish_marker', pattern: 'hammer' }
    const span: ChartMarker = {
      event_ts: END,
      kind: 'neutral_marker',
      pattern: 'doji',
      span_start_ts: START,
      span_end_ts: END,
    }
    expect(markerHighlightSpan([single, span])).toMatchObject({ startTs: START, endTs: END })
  })
})

describe('PatternSpanPrimitive highlight (Plan 0071 follow-up)', () => {
  function attach(barSpacing = 6): PatternSpanPrimitive {
    const timeScale = {
      timeToCoordinate: (t: UTCTimestamp): number | null =>
        t === toUtc(START) ? 100 : t === toUtc(END) ? 160 : null,
      options: (): { barSpacing: number } => ({ barSpacing }),
    }
    const p = new PatternSpanPrimitive(COLORS)
    p.attached({
      chart: { timeScale: () => timeScale },
      requestUpdate: () => {},
    } as unknown as SeriesAttachedParameter<Time>)
    return p
  }

  it('has no highlight rect and no extra pane view until setHighlight is called', () => {
    const p = attach()
    expect(p.currentHighlightRect()).toBeNull()
    expect(p.paneViews()).toHaveLength(0)
  })

  it('outlines the hovered span padded ~half a bar each side in the opaque token', () => {
    const p = attach(6)
    p.setHighlight(SPAN)
    const rect = p.currentHighlightRect()
    // pad = barSpacing/2 + 2 = 5 → [100-5, 160+5]
    expect(rect?.x1).toBe(95)
    expect(rect?.x2).toBe(165)
    // Opaque bullish token (not the translucent band alpha).
    expect(rect?.color).toBe('#00ff00')
  })

  it('adds a top-zOrder pane view while highlighting and drops it on clear', () => {
    const p = attach()
    p.setHighlight(SPAN)
    const views = p.paneViews()
    expect(views).toHaveLength(1)
    expect(views[0].zOrder?.()).toBe('top')

    p.setHighlight(null)
    expect(p.currentHighlightRect()).toBeNull()
    expect(p.paneViews()).toHaveLength(0)
  })
})
