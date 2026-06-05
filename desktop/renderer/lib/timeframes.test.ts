/**
 * Plan 0047 phase 5: the renderer timeframe vocabulary is a single source that
 * mirrors the backend canonical set. This pins the expected set so a silent
 * drift from `data/timeframes.py` (or a reintroduced 5m/1m) fails a test —
 * standing in for the `gen-types` parity that doesn't exist for this non-HTTP
 * registry.
 */
import { DEFAULT_TIMEFRAME, KNOWN_TIMEFRAMES, TIMEFRAMES, isTimeframe } from './timeframes'

describe('timeframe vocabulary', () => {
  it('is exactly the backend-supported set, cadence-ascending', () => {
    expect([...TIMEFRAMES]).toEqual(['15m', '1h', '4h', '1d', '1w'])
  })

  it('drops the unfetchable cadences that used to leak into the dropdown', () => {
    expect(TIMEFRAMES).not.toContain('5m')
    expect(TIMEFRAMES).not.toContain('1m')
  })

  it('KNOWN_TIMEFRAMES is derived from the same list (no second copy to drift)', () => {
    expect(KNOWN_TIMEFRAMES.size).toBe(TIMEFRAMES.length)
    for (const tf of TIMEFRAMES) {
      expect(KNOWN_TIMEFRAMES.has(tf)).toBe(true)
    }
  })

  it('the default timeframe is a member of the supported set', () => {
    expect(KNOWN_TIMEFRAMES.has(DEFAULT_TIMEFRAME)).toBe(true)
  })

  it('isTimeframe narrows supported values and rejects the rest', () => {
    expect(isTimeframe('4h')).toBe(true)
    expect(isTimeframe('1d')).toBe(true)
    expect(isTimeframe('5m')).toBe(false)
    expect(isTimeframe('nonsense')).toBe(false)
  })
})
