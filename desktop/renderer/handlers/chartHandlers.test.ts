/**
 * Direct unit tests for the chart-event reducer (Plan 0007 phase 4).
 * E2e covers the wire end-to-end; this file covers the state-transition
 * logic in isolation so a regression surfaces in seconds, not minutes.
 */
import {
  applyChartDivergences,
  applyChartHighlight,
  applyChartShow,
  applyChartTrendlines,
  applyChartUpdate,
  chartReducer,
  initialChartState,
} from './chartHandlers'
import type { Divergence, TrendlineSpec } from '../types/events'

const NOW_ISO = '2026-05-20T12:00:00.000Z'

const NECKLINE: TrendlineSpec = {
  points: [
    { ts: '2026-05-01T00:00:00Z', price: 100 },
    { ts: '2026-05-10T00:00:00Z', price: 104 },
  ],
  role: 'neckline',
  style: 'dashed',
  pattern: 'head_shoulders',
}

const DIVERGENCE: Divergence = {
  oscillator: 'rsi',
  kind: 'regular_bearish',
  price_pivots: [
    { ts: '2026-05-01T00:00:00Z', price: 120 },
    { ts: '2026-05-10T00:00:00Z', price: 124 },
  ],
  oscillator_pivots: [
    { ts: '2026-05-01T00:00:00Z', price: 78 },
    { ts: '2026-05-10T00:00:00Z', price: 71 },
  ],
  bar_index: 42,
  strength: 0.6,
}

function baseState() {
  return initialChartState(NOW_ISO)
}

describe('applyChartShow', () => {
  it('replaces symbol/timeframe/range and resets overlays + live highlights', () => {
    const prev = {
      ...baseState(),
      overlays: [{ kind: 'ema' as const, period: 20 }],
      liveHighlights: [{ event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker' as const }],
    }
    const next = applyChartShow(prev, {
      symbol: 'MSFT',
      timeframe: '1h',
      range_start: '2026-05-01T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
      overlays: [{ kind: 'ema', period: 50 }],
    })
    expect(next.symbol).toBe('MSFT')
    expect(next.timeframe).toBe('1h')
    expect(next.range_start).toBe('2026-05-01T00:00:00+00:00')
    expect(next.range_end).toBe('2026-05-20T00:00:00+00:00')
    expect(next.overlays).toEqual([{ kind: 'ema', period: 50 }])
    expect(next.liveHighlights).toEqual([])
  })

  it('treats missing overlays as []', () => {
    const next = applyChartShow(baseState(), {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    })
    expect(next.overlays).toEqual([])
  })

  it('PRESERVES trendlines on a same-chart show, CLEARS them on a symbol/timeframe switch (Plan 0064/ADR-0059)', () => {
    const prev = {
      ...baseState(),
      symbol: 'ES=F',
      timeframe: '1d' as const,
      trendlines: [NECKLINE],
    }

    // Same symbol+timeframe (e.g. an overlay/range refresh) → lines survive; the
    // recompute path keeps them current, so `chart.show` must not wipe them.
    const sameChart = applyChartShow(prev, {
      symbol: 'ES=F',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    })
    expect(sameChart.trendlines).toEqual([NECKLINE])

    // Different symbol → the geometry no longer belongs; clear it.
    const switched = applyChartShow(prev, {
      symbol: 'MSFT',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    })
    expect(switched.trendlines).toEqual([])

    // Different timeframe on the same symbol → also a switch → clear.
    const tfSwitch = applyChartShow(prev, {
      symbol: 'ES=F',
      timeframe: '1h',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    })
    expect(tfSwitch.trendlines).toEqual([])
  })

  it.each(['15m', '4h', '1w'])(
    'preserves the now-supported timeframe %s instead of coercing it to 1d',
    (timeframe) => {
      const next = applyChartShow(baseState(), {
        symbol: 'BTC-USD',
        timeframe,
        range_start: '2026-04-20T00:00:00+00:00',
        range_end: '2026-05-20T00:00:00+00:00',
      })
      expect(next.timeframe).toBe(timeframe)
    },
  )

  it('still narrows a genuinely-unsupported timeframe to the default (1d)', () => {
    const next = applyChartShow(baseState(), {
      symbol: 'BTC-USD',
      timeframe: '5m', // dropped from the supported set — defensive narrow
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    })
    expect(next.timeframe).toBe('1d')
  })
})

describe('applyChartUpdate', () => {
  it('merges overlays into an existing chart (continuation)', () => {
    const prev = {
      ...baseState(),
      overlays: [{ kind: 'ema' as const, period: 20 }],
    }
    const next = applyChartUpdate(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      overlays: [
        { kind: 'ema', period: 20 },
        { kind: 'ema', period: 50 },
      ],
    })
    expect(next.overlays).toEqual([
      { kind: 'ema', period: 20 },
      { kind: 'ema', period: 50 },
    ])
  })

  it('leaves range unchanged when payload omits range_start/range_end (continuation)', () => {
    const prev = baseState()
    const next = applyChartUpdate(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      overlays: [{ kind: 'ema', period: 50 }],
    })
    expect(next.range_start).toBe(prev.range_start)
    expect(next.range_end).toBe(prev.range_end)
  })

  it('narrows the visible range when payload supplies range_start/range_end', () => {
    const prev = baseState()
    const next = applyChartUpdate(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      range_start: '2026-05-10T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    })
    expect(next.range_start).toBe('2026-05-10T00:00:00+00:00')
    expect(next.range_end).toBe('2026-05-20T00:00:00+00:00')
  })

  it('preserves trendlines on a continuation update; clears them on a different-chart update (Plan 0064)', () => {
    const prev = { ...baseState(), trendlines: [NECKLINE] }
    // `chart.update` no longer carries trendlines. A continuation update leaves
    // the existing lines untouched (they ride `...prev`).
    const continuation = applyChartUpdate(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      overlays: [{ kind: 'ema', period: 50 }],
    })
    expect(continuation.trendlines).toEqual([NECKLINE])

    // A different-chart update falls back to chart.show semantics → clear.
    const otherChart = applyChartUpdate(prev, {
      symbol: 'MSFT',
      timeframe: '1h',
      overlays: [{ kind: 'ema', period: 50 }],
    })
    expect(otherChart.trendlines).toEqual([])
  })

  it('out-of-order: update for a different symbol falls back to chart.show semantics', () => {
    const prev = baseState()
    const next = applyChartUpdate(prev, {
      symbol: 'MSFT',
      timeframe: '1h',
      overlays: [{ kind: 'ema', period: 50 }],
    })
    expect(next.symbol).toBe('MSFT')
    expect(next.timeframe).toBe('1h')
    expect(next.overlays).toEqual([{ kind: 'ema', period: 50 }])
    // Range was not in the payload — falls back to prev's range (some
    // window is better than blank, per ADR-0017's available-fields rule).
    expect(next.range_start).toBe(prev.range_start)
    expect(next.range_end).toBe(prev.range_end)
    // Live highlights from the previous chart cleared — that buffer was
    // about a different symbol+timeframe.
    expect(next.liveHighlights).toEqual([])
  })
})

describe('applyChartHighlight', () => {
  it('buffers markers for the active chart', () => {
    const prev = baseState()
    const next = applyChartHighlight(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      markers: [{ event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker', label: 'hammer' }],
    })
    expect(next.liveHighlights).toHaveLength(1)
    expect(next.liveHighlights[0].label).toBe('hammer')
  })

  it('drops highlights for a non-active chart', () => {
    const prev = baseState()
    const next = applyChartHighlight(prev, {
      symbol: 'MSFT',
      timeframe: '1h',
      markers: [{ event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker' }],
    })
    expect(next).toBe(prev)
  })

  it('dedups by (event_ts, kind)', () => {
    const prev = {
      ...baseState(),
      liveHighlights: [
        { event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker' as const, label: 'first' },
      ],
    }
    const next = applyChartHighlight(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      markers: [
        // same key — should NOT add a second entry
        { event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker', label: 'second' },
        // different ts — should add
        { event_ts: '2026-05-16T00:00:00Z', kind: 'bullish_marker', label: 'fresh' },
      ],
    })
    expect(next.liveHighlights).toHaveLength(2)
    expect(next.liveHighlights[0].label).toBe('first')
    expect(next.liveHighlights[1].label).toBe('fresh')
  })

  it('keeps two same-bar+same-kind markers with DIFFERENT patterns; dedups identical (Plan 0049)', () => {
    const prev = {
      ...baseState(),
      liveHighlights: [
        { event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker' as const, pattern: 'hammer' },
      ],
    }
    const next = applyChartHighlight(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      markers: [
        // same event_ts AND kind as the buffered hammer, but a DIFFERENT pattern
        // → must survive (the old (event_ts, kind) collision is gone)
        { event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker', pattern: 'bullish_engulfing' },
        // an exact duplicate of the buffered hammer (event_ts+pattern+kind) → NOT added
        { event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker', pattern: 'hammer' },
      ],
    })
    expect(next.liveHighlights).toHaveLength(2)
    expect(next.liveHighlights.map((m) => m.pattern)).toEqual(['hammer', 'bullish_engulfing'])
  })
})

describe('applyChartTrendlines (Plan 0064/ADR-0059)', () => {
  it('adds the lines when the payload matches the active chart', () => {
    const prev = baseState()
    const next = applyChartTrendlines(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      trendlines: [NECKLINE],
    })
    expect(next.trendlines).toEqual([NECKLINE])
  })

  it('drops trendlines for a non-active chart (symbol OR timeframe mismatch)', () => {
    const prev = { ...baseState(), trendlines: [NECKLINE] }
    const symbolMismatch = applyChartTrendlines(prev, {
      symbol: 'MSFT',
      timeframe: prev.timeframe,
      trendlines: [{ ...NECKLINE, style: 'solid' }],
    })
    expect(symbolMismatch).toBe(prev) // unchanged reference

    const tfMismatch = applyChartTrendlines(prev, {
      symbol: prev.symbol,
      timeframe: '1h',
      trendlines: [{ ...NECKLINE, style: 'solid' }],
    })
    expect(tfMismatch).toBe(prev)
  })

  it('replaces the current lines with the payload set (last recompute wins)', () => {
    const prev = { ...baseState(), trendlines: [NECKLINE] }
    const confirmed: TrendlineSpec = { ...NECKLINE, style: 'solid' }
    const next = applyChartTrendlines(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      trendlines: [confirmed],
    })
    expect(next.trendlines).toEqual([confirmed])
  })

  it('a same-chart chart.show AFTER trendlines preserves them; a switch clears them', () => {
    let state = applyChartTrendlines(baseState(), {
      symbol: 'AAPL',
      timeframe: '1d',
      trendlines: [NECKLINE],
    })
    // Plain same-chart show (no trendline concept on the wire) must not wipe them.
    state = chartReducer(state, {
      kind: 'event/chart.show',
      payload: {
        symbol: 'AAPL',
        timeframe: '1d',
        range_start: '2026-04-20T00:00:00+00:00',
        range_end: '2026-05-20T00:00:00+00:00',
      },
    })
    expect(state.trendlines).toEqual([NECKLINE])

    // A switch clears.
    state = chartReducer(state, {
      kind: 'event/chart.show',
      payload: {
        symbol: 'MSFT',
        timeframe: '1d',
        range_start: '2026-04-20T00:00:00+00:00',
        range_end: '2026-05-20T00:00:00+00:00',
      },
    })
    expect(state.trendlines).toEqual([])
  })
})

describe('applyChartDivergences (Plan 0091/ADR-0090)', () => {
  it('adds the divergences when the payload matches the active chart', () => {
    const prev = baseState()
    const next = applyChartDivergences(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      divergences: [DIVERGENCE],
    })
    expect(next.divergences).toEqual([DIVERGENCE])
  })

  it('drops divergences for a non-active chart (symbol OR timeframe mismatch)', () => {
    const prev = { ...baseState(), divergences: [DIVERGENCE] }
    const symbolMismatch = applyChartDivergences(prev, {
      symbol: 'MSFT',
      timeframe: prev.timeframe,
      divergences: [{ ...DIVERGENCE, kind: 'hidden_bullish' }],
    })
    expect(symbolMismatch).toBe(prev) // unchanged reference

    const tfMismatch = applyChartDivergences(prev, {
      symbol: prev.symbol,
      timeframe: '1h',
      divergences: [{ ...DIVERGENCE, kind: 'hidden_bullish' }],
    })
    expect(tfMismatch).toBe(prev)
  })

  it('replaces the current divergences with the payload set (last push wins)', () => {
    const prev = { ...baseState(), divergences: [DIVERGENCE] }
    const hiddenBull: Divergence = { ...DIVERGENCE, kind: 'hidden_bullish' }
    const next = applyChartDivergences(prev, {
      symbol: prev.symbol,
      timeframe: prev.timeframe,
      divergences: [hiddenBull],
    })
    expect(next.divergences).toEqual([hiddenBull])
  })

  it('a same-chart chart.show AFTER divergences preserves them; a switch clears them', () => {
    let state = applyChartDivergences(baseState(), {
      symbol: 'AAPL',
      timeframe: '1d',
      divergences: [DIVERGENCE],
    })
    // Push-only (no recompute-on-load), so a plain same-chart show must not wipe them.
    state = chartReducer(state, {
      kind: 'event/chart.show',
      payload: {
        symbol: 'AAPL',
        timeframe: '1d',
        range_start: '2026-04-20T00:00:00+00:00',
        range_end: '2026-05-20T00:00:00+00:00',
      },
    })
    expect(state.divergences).toEqual([DIVERGENCE])

    // A symbol switch clears (geometry belongs to its chart).
    state = chartReducer(state, {
      kind: 'event/chart.show',
      payload: {
        symbol: 'MSFT',
        timeframe: '1d',
        range_start: '2026-04-20T00:00:00+00:00',
        range_end: '2026-05-20T00:00:00+00:00',
      },
    })
    expect(state.divergences).toEqual([])
  })
})

describe('chartReducer ui actions', () => {
  it('ui/set-symbol clears the live-highlights buffer, the trendlines, and the divergences', () => {
    const prev = {
      ...baseState(),
      liveHighlights: [{ event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker' as const }],
      trendlines: [NECKLINE],
      divergences: [DIVERGENCE],
    }
    const next = chartReducer(prev, { kind: 'ui/set-symbol', symbol: 'MSFT' })
    expect(next.symbol).toBe('MSFT')
    expect(next.liveHighlights).toEqual([])
    // Trendline + divergence geometry belongs to the chart it was computed for.
    expect(next.trendlines).toEqual([])
    expect(next.divergences).toEqual([])
  })

  it('ui/set-timeframe clears the trendlines and divergences (geometry is per symbol+timeframe)', () => {
    const prev = { ...baseState(), trendlines: [NECKLINE], divergences: [DIVERGENCE] }
    const next = chartReducer(prev, { kind: 'ui/set-timeframe', timeframe: '1h' })
    expect(next.timeframe).toBe('1h')
    expect(next.trendlines).toEqual([])
    expect(next.divergences).toEqual([])
  })

  it('ui/refresh recomputes range_start = nowIso - lookbackDays', () => {
    const prev = baseState()
    const next = chartReducer(prev, {
      kind: 'ui/refresh',
      nowIso: '2026-06-20T00:00:00.000Z',
      lookbackDays: 30,
    })
    expect(next.range_end).toBe('2026-06-20T00:00:00.000Z')
    expect(next.range_start).toBe('2026-05-21T00:00:00.000Z')
  })
})
