/**
 * Plan 0029 phase 2: direct unit test for `annotationsToMarkers` (relocated
 * from the old `CandlestickChart.test.ts` when the function moved to `lib/`).
 *
 * Table-driven over the kind→shape/position/color mapping, with hand-computed
 * `time` values (epoch seconds = `Date.UTC(...) / 1000`), label truncation, the
 * null-label case, ascending-time sort (lightweight-charts throws otherwise),
 * and the empty-input case.
 */
import type { Annotation } from '../types/sidecar/annotation'
import { annotationsToMarkers } from './markers'

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
  it.each([
    {
      name: 'bullish_marker → belowBar + arrowUp + green',
      kind: 'bullish_marker' as const,
      position: 'belowBar',
      shape: 'arrowUp',
      color: '#16a34a',
    },
    {
      name: 'bearish_marker → aboveBar + arrowDown + red',
      kind: 'bearish_marker' as const,
      position: 'aboveBar',
      shape: 'arrowDown',
      color: '#dc2626',
    },
  ])('$name', ({ kind, position, shape, color }) => {
    const [marker] = annotationsToMarkers([annotation({ kind })])
    expect(marker.position).toBe(position)
    expect(marker.shape).toBe(shape)
    expect(marker.color).toBe(color)
  })

  it('converts event_ts to epoch-second time (hand-computed)', () => {
    const [marker] = annotationsToMarkers([annotation({ event_ts: '2026-04-15T00:00:00+00:00' })])
    expect(marker.time).toBe(Date.UTC(2026, 3, 15, 0, 0, 0) / 1000)
  })

  it('uses the annotation label as marker text', () => {
    const [marker] = annotationsToMarkers([annotation({ label: 'hammer at support' })])
    expect(marker.text).toBe('hammer at support')
  })

  it('renders an empty text field when label is null', () => {
    const [marker] = annotationsToMarkers([annotation({ label: null })])
    expect(marker.text).toBe('')
  })

  it('truncates a 25-char label to 24 chars with a trailing ellipsis', () => {
    const long = 'a'.repeat(80)
    const [marker] = annotationsToMarkers([annotation({ label: long })])
    expect(marker.text).toBe(`${'a'.repeat(23)}…`)
    expect(marker.text!.length).toBe(24)
  })

  it('leaves a label of exactly 24 chars untouched (boundary)', () => {
    const exact = 'a'.repeat(24)
    const [marker] = annotationsToMarkers([annotation({ label: exact })])
    expect(marker.text).toBe(exact)
  })

  it('sorts markers ascending by time', () => {
    const out = annotationsToMarkers([
      annotation({ id: 'b', event_ts: '2026-04-20T00:00:00+00:00' }),
      annotation({ id: 'a', event_ts: '2026-04-10T00:00:00+00:00' }),
      annotation({ id: 'c', event_ts: '2026-04-15T00:00:00+00:00' }),
    ])
    expect(out.map((m) => m.time)).toEqual([
      Date.UTC(2026, 3, 10) / 1000,
      Date.UTC(2026, 3, 15) / 1000,
      Date.UTC(2026, 3, 20) / 1000,
    ])
  })

  it('returns an empty array for empty input', () => {
    expect(annotationsToMarkers([])).toEqual([])
  })
})
