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
import { annotationsToMarkers, markerVisual } from './markers'

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

  it('sets a larger-than-default marker size on every marker (visibility bump)', () => {
    const [marker] = annotationsToMarkers([annotation()])
    // lightweight-charts' default size is 1; the baseline must be clearly bigger.
    expect(marker.size).toBeGreaterThan(1)
  })

  it('neutral_marker → inBar + circle + neutral token (Plan 0049)', () => {
    const [marker] = annotationsToMarkers(
      [{ event_ts: '2026-04-15T00:00:00+00:00', kind: 'neutral_marker', label: 'doji' }],
      { bullish: '#16a34a', bearish: '#dc2626', neutral: '#64748b' },
    )
    expect(marker.position).toBe('inBar')
    expect(marker.shape).toBe('circle')
    expect(marker.color).toBe('#64748b')
  })

  it('scales the glyph from a live marker strength (sweep path)', () => {
    const strong = annotationsToMarkers([
      { event_ts: '2026-04-15T00:00:00+00:00', kind: 'bullish_marker', strength: 0.99 },
    ])[0]
    const weak = annotationsToMarkers([
      { event_ts: '2026-04-15T00:00:00+00:00', kind: 'bullish_marker', strength: 0.1 },
    ])[0]
    expect(strong.size ?? 0).toBeGreaterThan(weak.size ?? 0)
  })
})

describe('markerVisual (strength → size + intensity)', () => {
  // Deliberately not the default hex, so an assertion that the result derives
  // from these proves the color is token-driven, not hardcoded.
  const TOKENS = { bullish: '#00ff00', bearish: '#0000ff', neutral: '#888888' }

  it('maps a strong bearish marker to a larger size and more-intense token than a weak one', () => {
    const strong = markerVisual('bearish_marker', 0.99, TOKENS)
    const weak = markerVisual('bearish_marker', 0.15, TOKENS)

    expect(strong.size).toBeGreaterThan(weak.size)
    // Both derive from the passed bearish token (not a hardcoded red)…
    expect(strong.color.startsWith(TOKENS.bearish)).toBe(true)
    expect(weak.color.startsWith(TOKENS.bearish)).toBe(true)
    // …and the strong marker is more intense (higher alpha byte).
    const alphaByte = (c: string): number => parseInt(c.slice(7, 9) || 'ff', 16)
    expect(alphaByte(strong.color)).toBeGreaterThan(alphaByte(weak.color))
  })

  it('resolves bullish vs bearish to the passed direction tokens (not hardcoded hex)', () => {
    expect(markerVisual('bullish_marker', 1, TOKENS).color.startsWith(TOKENS.bullish)).toBe(true)
    expect(markerVisual('bearish_marker', 1, TOKENS).color.startsWith(TOKENS.bearish)).toBe(true)
  })

  it('resolves neutral to the passed neutral token (Plan 0049)', () => {
    expect(markerVisual('neutral_marker', null, TOKENS).color).toBe(TOKENS.neutral)
    expect(markerVisual('neutral_marker', 1, TOKENS).color.startsWith(TOKENS.neutral)).toBe(true)
  })

  it('treats unknown strength (null) as a full-intensity token at the baseline size', () => {
    const v = markerVisual('bullish_marker', null, TOKENS)
    expect(v.color).toBe(TOKENS.bullish) // plain token, no alpha byte
    expect(v.size).toBeGreaterThan(1)
    // …and smaller than a max-strength marker, so strong still reads strongest.
    expect(v.size).toBeLessThan(markerVisual('bullish_marker', 1, TOKENS).size)
  })

  it('clamps out-of-range strength to [0,1]', () => {
    expect(markerVisual('bullish_marker', 5, TOKENS).size).toBe(
      markerVisual('bullish_marker', 1, TOKENS).size,
    )
    expect(markerVisual('bullish_marker', -3, TOKENS).size).toBe(
      markerVisual('bullish_marker', 0, TOKENS).size,
    )
  })
})
