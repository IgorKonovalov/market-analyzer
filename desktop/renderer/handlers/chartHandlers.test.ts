/**
 * Direct unit tests for the chart-event reducer (Plan 0007 phase 4).
 * E2e covers the wire end-to-end; this file covers the state-transition
 * logic in isolation so a regression surfaces in seconds, not minutes.
 */
import {
  applyChartHighlight,
  applyChartShow,
  applyChartUpdate,
  chartReducer,
  initialChartState,
} from './chartHandlers'

const NOW_ISO = '2026-05-20T12:00:00.000Z'

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
})

describe('chartReducer ui actions', () => {
  it('ui/set-symbol clears the live-highlights buffer', () => {
    const prev = {
      ...baseState(),
      liveHighlights: [{ event_ts: '2026-05-15T00:00:00Z', kind: 'bullish_marker' as const }],
    }
    const next = chartReducer(prev, { kind: 'ui/set-symbol', symbol: 'MSFT' })
    expect(next.symbol).toBe('MSFT')
    expect(next.liveHighlights).toEqual([])
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
