/**
 * Plan 0104 phase 3: the position ordering invariant (`lib/positions.ts`).
 *
 * The `stop < entry < target` (long) / `target < entry < stop` (short) rule lives
 * in one pure module so it is unit-tested apart from the pointer machinery (the
 * plan's risk mitigation). Covers the default 2:1 levels, derived R:R, the drag
 * clamp, and the persistence validity check.
 */
import type { DrawingSpec } from '../types/events'
import {
  applyPositionHandleDrag,
  defaultPositionLevels,
  enforcePositionOrder,
  isPositionKind,
  positionLevelsValid,
  riskReward,
} from './positions'

describe('isPositionKind', () => {
  it('is true for the two position kinds only', () => {
    expect(isPositionKind('long_position')).toBe(true)
    expect(isPositionKind('short_position')).toBe(true)
    expect(isPositionKind('date_range')).toBe(false)
    expect(isPositionKind('trendline')).toBe(false)
  })
})

describe('defaultPositionLevels', () => {
  it('straddles entry 2:1 for a long (stop below, target above)', () => {
    const { stop, target } = defaultPositionLevels('long_position', 100)
    expect(stop).toBeCloseTo(99, 6)
    expect(target).toBeCloseTo(102, 6)
    expect(stop).toBeLessThan(100)
    expect(target).toBeGreaterThan(100)
    // 2:1 reward:risk by construction.
    expect(riskReward(100, stop, target)).toBeCloseTo(2, 6)
  })

  it('mirrors for a short (stop above, target below)', () => {
    const { stop, target } = defaultPositionLevels('short_position', 100)
    expect(stop).toBeGreaterThan(100)
    expect(target).toBeLessThan(100)
    expect(riskReward(100, stop, target)).toBeCloseTo(2, 6)
  })
})

describe('riskReward', () => {
  it('is |target−entry| / |entry−stop|', () => {
    expect(riskReward(100, 95, 115)).toBeCloseTo(3, 6) // 15/5
  })
  it('is null for a degenerate risk leg', () => {
    expect(riskReward(100, 100, 110)).toBeNull()
  })
})

describe('enforcePositionOrder — the clamp', () => {
  it('pins a long stop dragged through entry to just below it (invariant preserved)', () => {
    const out = enforcePositionOrder('long_position', 100, 105, 110) // stop above entry
    expect(out.stop).toBeLessThan(100)
    expect(out.target).toBe(110)
  })

  it('pins a short stop dragged through entry to just above it', () => {
    const out = enforcePositionOrder('short_position', 100, 95, 90) // stop below entry
    expect(out.stop).toBeGreaterThan(100)
    expect(out.target).toBe(90)
  })

  it('leaves already-valid levels untouched', () => {
    const out = enforcePositionOrder('long_position', 100, 95, 110)
    expect(out).toEqual({ entry: 100, stop: 95, target: 110 })
  })
})

describe('positionLevelsValid', () => {
  it('accepts strictly-ordered long/short levels', () => {
    expect(positionLevelsValid('long_position', 100, 95, 110)).toBe(true)
    expect(positionLevelsValid('short_position', 100, 110, 90)).toBe(true)
  })
  it('rejects a violated ordering or a non-finite level', () => {
    expect(positionLevelsValid('long_position', 100, 105, 110)).toBe(false) // stop above entry
    expect(positionLevelsValid('long_position', 100, 95, 90)).toBe(false) // target below entry
    expect(positionLevelsValid('long_position', 100, null, 110)).toBe(false)
    expect(positionLevelsValid('short_position', 100, 110, Number.NaN)).toBe(false)
  })
})

describe('applyPositionHandleDrag', () => {
  const long: DrawingSpec = {
    kind: 'long_position',
    points: [{ ts: '2026-05-01T00:00:00Z', price: 100 }],
    stop: 95,
    target: 110,
    provenance: 'user',
    id: 'p1',
  }

  it('moves the entry anchor (handle 0) in time+price and re-clamps the levels', () => {
    const moved = applyPositionHandleDrag(long, 0, { ts: '2026-05-02T00:00:00Z', price: 108 })
    expect(moved.points).toEqual([{ ts: '2026-05-02T00:00:00Z', price: 108 }])
    // Entry rose to 108 (above the old stop 95, below target 110) → still valid.
    expect(moved.stop).toBe(95)
    expect(moved.target).toBe(110)
  })

  it('drags the stop handle (handle 1) in price only, keeping the entry anchor', () => {
    const moved = applyPositionHandleDrag(long, 1, { ts: '2026-05-09T00:00:00Z', price: 92 })
    expect(moved.points).toEqual(long.points) // entry anchor unchanged
    expect(moved.stop).toBe(92)
    expect(moved.target).toBe(110)
  })

  it('clamps a stop dragged through the entry (handle 1)', () => {
    const moved = applyPositionHandleDrag(long, 1, { ts: '2026-05-09T00:00:00Z', price: 130 })
    expect(moved.stop).toBeLessThan(100) // pinned below entry, not flipped
  })

  it('drags the target handle (handle 2) in price only', () => {
    const moved = applyPositionHandleDrag(long, 2, { ts: '2026-05-09T00:00:00Z', price: 125 })
    expect(moved.target).toBe(125)
    expect(moved.stop).toBe(95)
  })
})
