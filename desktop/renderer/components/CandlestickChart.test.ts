/**
 * Plan 0006 phase 6 done-when: bullish/bearish kinds map to the correct
 * lightweight-charts marker shape, position, and color. Labels are truncated
 * so a runaway agent label can't blow out the chart tooltip layer. Markers
 * are sorted ascending by time (lightweight-charts throws on out-of-order).
 */
import type { Annotation } from '../types/sidecar/annotation'
import { annotationsToMarkers } from './CandlestickChart'

function annotation(overrides: Partial<Annotation> = {}): Annotation {
  return {
    id: 'ann-1',
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: '2026-04-15T00:00:00+00:00',
    kind: 'bullish_marker',
    label: 'hammer',
    agent_id: 'test',
    created_at: '2026-04-15T01:00:00+00:00',
    ...overrides,
  }
}

describe('annotationsToMarkers', () => {
  it('maps bullish_marker to belowBar + arrowUp', () => {
    const [marker] = annotationsToMarkers([annotation({ kind: 'bullish_marker' })])
    expect(marker.position).toBe('belowBar')
    expect(marker.shape).toBe('arrowUp')
    expect(marker.color).toMatch(/^#/)
  })

  it('maps bearish_marker to aboveBar + arrowDown', () => {
    const [marker] = annotationsToMarkers([annotation({ kind: 'bearish_marker' })])
    expect(marker.position).toBe('aboveBar')
    expect(marker.shape).toBe('arrowDown')
    expect(marker.color).toMatch(/^#/)
  })

  it('uses the annotation label as marker text', () => {
    const [marker] = annotationsToMarkers([annotation({ label: 'hammer at support' })])
    expect(marker.text).toBe('hammer at support')
  })

  it('renders an empty text field when label is null', () => {
    const [marker] = annotationsToMarkers([annotation({ label: null })])
    expect(marker.text).toBe('')
  })

  it('truncates labels longer than ~24 chars with an ellipsis', () => {
    const long = 'a'.repeat(80)
    const [marker] = annotationsToMarkers([annotation({ label: long })])
    expect(marker.text).toBeDefined()
    expect(marker.text!.length).toBeLessThanOrEqual(24)
    expect(marker.text!.endsWith('…')).toBe(true)
  })

  it('sorts markers ascending by time', () => {
    const out = annotationsToMarkers([
      annotation({ id: 'b', event_ts: '2026-04-20T00:00:00+00:00' }),
      annotation({ id: 'a', event_ts: '2026-04-10T00:00:00+00:00' }),
      annotation({ id: 'c', event_ts: '2026-04-15T00:00:00+00:00' }),
    ])
    const times = out.map((m) => m.time as number)
    expect(times).toEqual([...times].sort((a, b) => a - b))
  })

  it('returns an empty array for empty input', () => {
    expect(annotationsToMarkers([])).toEqual([])
  })
})
