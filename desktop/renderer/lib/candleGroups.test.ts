/**
 * Plan 0071 phase 2: unit test for the pure candlestick-marker grouping. Covers
 * the (pattern type, direction) grouping + counts + first-seen order, the
 * most-recent-group default selection, the layer-id round-trip, and the display
 * labels (known map + humanised fallback + null).
 */
import type { ChartMarker } from './markers'
import {
  CANDLE_LAYER_ID,
  candleDirectionLabel,
  candleGroupKeyFromLayerId,
  candleGroupLabel,
  candleGroupLayerId,
  candlePatternDisplayName,
  groupCandlestickMarkers,
  mostRecentGroupKey,
} from './candleGroups'

function marker(overrides: Partial<ChartMarker> = {}): ChartMarker {
  return {
    event_ts: '2026-04-15T00:00:00+00:00',
    kind: 'bullish_marker',
    pattern: 'hammer',
    ...overrides,
  }
}

describe('groupCandlestickMarkers', () => {
  it('groups by (pattern type, direction) with instance counts', () => {
    const groups = groupCandlestickMarkers([
      marker({ pattern: 'hammer', kind: 'bullish_marker' }),
      marker({ pattern: 'hammer', kind: 'bullish_marker' }),
      marker({ pattern: 'doji', kind: 'neutral_marker' }),
    ])
    expect(groups).toHaveLength(2)
    const hammer = groups.find((g) => g.key === 'hammer|bullish_marker')
    expect(hammer?.count).toBe(2)
    expect(groups.find((g) => g.key === 'doji|neutral_marker')?.count).toBe(1)
  })

  it('keeps same-bar opposite-direction hits in separate groups (ADR-0045 lesson)', () => {
    const groups = groupCandlestickMarkers([
      marker({ pattern: 'engulfing', kind: 'bullish_marker' }),
      marker({ pattern: 'engulfing', kind: 'bearish_marker' }),
    ])
    expect(groups.map((g) => g.key)).toEqual([
      'engulfing|bullish_marker',
      'engulfing|bearish_marker',
    ])
  })

  it('groups a pattern-less marker under `unknown|<kind>`', () => {
    const [group] = groupCandlestickMarkers([marker({ pattern: null, kind: 'bearish_marker' })])
    expect(group.key).toBe('unknown|bearish_marker')
    expect(group.pattern).toBeNull()
  })

  it('preserves first-seen order', () => {
    const groups = groupCandlestickMarkers([
      marker({ pattern: 'doji', kind: 'neutral_marker' }),
      marker({ pattern: 'hammer', kind: 'bullish_marker' }),
    ])
    expect(groups.map((g) => g.pattern)).toEqual(['doji', 'hammer'])
  })

  it('tracks the newest event_ts per group as latestTs', () => {
    const [group] = groupCandlestickMarkers([
      marker({ event_ts: '2026-04-10T00:00:00+00:00' }),
      marker({ event_ts: '2026-04-20T00:00:00+00:00' }),
      marker({ event_ts: '2026-04-15T00:00:00+00:00' }),
    ])
    expect(group.latestTs).toBe('2026-04-20T00:00:00+00:00')
  })

  it('returns no groups for no markers', () => {
    expect(groupCandlestickMarkers([])).toEqual([])
  })
})

describe('mostRecentGroupKey', () => {
  it('picks the group holding the newest marker', () => {
    const groups = groupCandlestickMarkers([
      marker({ pattern: 'hammer', kind: 'bullish_marker', event_ts: '2026-04-10T00:00:00+00:00' }),
      marker({ pattern: 'doji', kind: 'neutral_marker', event_ts: '2026-04-25T00:00:00+00:00' }),
    ])
    expect(mostRecentGroupKey(groups)).toBe('doji|neutral_marker')
  })

  it('returns null for an empty group list', () => {
    expect(mostRecentGroupKey([])).toBeNull()
  })
})

describe('layer id round-trip', () => {
  it('candleGroupKeyFromLayerId reverses candleGroupLayerId', () => {
    const key = 'bullish_engulfing|bullish_marker'
    expect(candleGroupKeyFromLayerId(candleGroupLayerId(key))).toBe(key)
  })

  it('returns null for a non-candlestick-group id (master, overlay, etc.)', () => {
    expect(candleGroupKeyFromLayerId('candles-master')).toBeNull()
    expect(candleGroupKeyFromLayerId('overlay:ema:20')).toBeNull()
    expect(candleGroupKeyFromLayerId('trendlines:head_shoulders|solid')).toBeNull()
  })

  it('namespaces group ids under the candlestick layer id', () => {
    expect(candleGroupLayerId('doji|neutral_marker')).toBe(`${CANDLE_LAYER_ID}:doji|neutral_marker`)
  })
})

describe('labels', () => {
  it('maps a known pattern to its display name', () => {
    expect(candlePatternDisplayName('three_white_soldiers')).toBe('Three white soldiers')
    expect(candlePatternDisplayName('bullish_engulfing')).toBe('Bullish engulfing')
  })

  it('humanises an unknown pattern token', () => {
    expect(candlePatternDisplayName('some_new_pattern')).toBe('Some new pattern')
  })

  it('renders null pattern as the generic "Pattern"', () => {
    expect(candlePatternDisplayName(null)).toBe('Pattern')
  })

  it('maps a marker kind to its direction word', () => {
    expect(candleDirectionLabel('bullish_marker')).toBe('bullish')
    expect(candleDirectionLabel('bearish_marker')).toBe('bearish')
    expect(candleDirectionLabel('neutral_marker')).toBe('neutral')
  })

  it('composes a full group label: `<Pattern> (<direction>)`', () => {
    const [group] = groupCandlestickMarkers([marker({ pattern: 'doji', kind: 'neutral_marker' })])
    expect(candleGroupLabel(group)).toBe('Doji (neutral)')
  })
})
