/**
 * Plan 0082 phase 3 (ADR-0077): the renderer-owned user-overlay store + merge.
 *
 * Defends: add/load round-trip + persistence, `(symbol, timeframe)` isolation,
 * remove (with empty-bucket drop), dedup by overlayKey, the price_line/unknown
 * scope guard, storage bounds, graceful degradation on blocked/malformed storage,
 * and the pure `mergeOverlays` union (dedup + user-key provenance + stickiness).
 */
import { mergeOverlays } from './userOverlays'
import type { OverlaySpec } from '../types/events'

const EMA20: OverlaySpec = { kind: 'ema', period: 20 }
const SMA50: OverlaySpec = { kind: 'sma', period: 50 }
const BB: OverlaySpec = { kind: 'bbands', period: 20, multiplier: 2 }

describe('mergeOverlays (pure union + provenance)', () => {
  it('appends user overlays after agent overlays and reports the user keys', () => {
    const { overlays, userKeys } = mergeOverlays([EMA20], [BB])
    expect(overlays).toEqual([EMA20, BB])
    expect([...userKeys]).toEqual(['bbands:20'])
  })

  it('collapses an identical agent + user spec to one drawn series (agent wins)', () => {
    const { overlays, userKeys } = mergeOverlays([EMA20], [EMA20])
    expect(overlays).toEqual([EMA20]) // one series, not two
    expect([...userKeys]).toEqual(['ema:20']) // still user-removable
  })

  it('handles an undefined agent layer (user-only)', () => {
    const { overlays, userKeys } = mergeOverlays(undefined, [SMA50])
    expect(overlays).toEqual([SMA50])
    expect([...userKeys]).toEqual(['sma:50'])
  })

  it('does not mutate its inputs', () => {
    const agent = [EMA20]
    const user = [BB]
    mergeOverlays(agent, user)
    expect(agent).toEqual([EMA20])
    expect(user).toEqual([BB])
  })
})

describe('userOverlays store', () => {
  let mod: typeof import('./userOverlays')
  const freshModule = (): typeof import('./userOverlays') => {
    jest.resetModules()
    return require('./userOverlays') as typeof import('./userOverlays')
  }

  beforeEach(() => {
    window.localStorage.clear()
    mod = freshModule()
  })

  it('adds and loads back for the same (symbol, timeframe), and persists to localStorage', () => {
    mod.addUserOverlay('BTC-USD', '1d', BB)
    expect(mod.loadUserOverlays('BTC-USD', '1d')).toEqual([BB])
    const raw = window.localStorage.getItem('ma.userOverlays')
    expect(raw).not.toBeNull()
    expect(JSON.parse(raw as string)[mod.userOverlayStoreKey('BTC-USD', '1d')]).toEqual([BB])
  })

  it('reloads persisted overlays into a fresh module instance', () => {
    mod.addUserOverlay('BTC-USD', '1d', BB)
    const reloaded = freshModule()
    expect(reloaded.loadUserOverlays('BTC-USD', '1d')).toEqual([BB])
  })

  it('isolates buckets by (symbol, timeframe)', () => {
    mod.addUserOverlay('BTC-USD', '1d', BB)
    mod.addUserOverlay('ETH-USD', '1h', EMA20)
    expect(mod.loadUserOverlays('BTC-USD', '1d')).toEqual([BB])
    expect(mod.loadUserOverlays('ETH-USD', '1h')).toEqual([EMA20])
    expect(mod.loadUserOverlays('BTC-USD', '1h')).toEqual([]) // different tf, empty
  })

  it('removes an overlay by overlayKey and drops the emptied bucket', () => {
    mod.addUserOverlay('BTC-USD', '1d', BB)
    mod.removeUserOverlay('BTC-USD', '1d', BB)
    expect(mod.loadUserOverlays('BTC-USD', '1d')).toEqual([])
    // The emptied bucket is gone from storage, not retained empty.
    const raw = window.localStorage.getItem('ma.userOverlays')
    expect(JSON.parse(raw as string)[mod.userOverlayStoreKey('BTC-USD', '1d')]).toBeUndefined()
  })

  it('dedups by overlayKey — re-adding the same kind+period replaces, latest wins', () => {
    mod.addUserOverlay('BTC-USD', '1d', { kind: 'bbands', period: 20, multiplier: 2 })
    mod.addUserOverlay('BTC-USD', '1d', { kind: 'bbands', period: 20, multiplier: 3 })
    const overlays = mod.loadUserOverlays('BTC-USD', '1d')
    expect(overlays).toHaveLength(1)
    expect(overlays[0].multiplier).toBe(3)
  })

  it('ignores a non-storable kind (price_line, unknown)', () => {
    mod.addUserOverlay('BTC-USD', '1d', {
      kind: 'price_line',
      price: 100,
      label: 'R',
    } as OverlaySpec)
    mod.addUserOverlay('BTC-USD', '1d', { kind: 'rsi', period: 14 } as OverlaySpec)
    expect(mod.loadUserOverlays('BTC-USD', '1d')).toEqual([])
  })

  it('notifies subscribers on mutation', () => {
    const cb = jest.fn()
    const unsub = mod.subscribeUserOverlays(cb)
    mod.addUserOverlay('BTC-USD', '1d', BB)
    expect(cb).toHaveBeenCalledTimes(1)
    unsub()
    mod.addUserOverlay('BTC-USD', '1d', EMA20)
    expect(cb).toHaveBeenCalledTimes(1) // no further calls after unsubscribe
  })

  it('does not throw when localStorage.setItem is blocked, and keeps the in-memory copy', () => {
    const setItem = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceeded')
    })
    expect(() => mod.addUserOverlay('BTC-USD', '1d', BB)).not.toThrow()
    // In-memory source of truth still reflects the add (session-only).
    expect(mod.loadUserOverlays('BTC-USD', '1d')).toEqual([BB])
    setItem.mockRestore()
  })

  it('degrades to empty on malformed stored JSON', () => {
    window.localStorage.setItem('ma.userOverlays', '{ not json')
    const reloaded = freshModule()
    expect(reloaded.loadUserOverlays('BTC-USD', '1d')).toEqual([])
  })

  it('drops price_line/unknown entries when sanitizing a persisted store', () => {
    window.localStorage.setItem(
      'ma.userOverlays',
      JSON.stringify({
        [mod.userOverlayStoreKey('BTC-USD', '1d')]: [
          { kind: 'ema', period: 20 },
          { kind: 'price_line', price: 100, label: 'R' },
          { kind: 'garbage' },
        ],
      }),
    )
    const reloaded = freshModule()
    expect(reloaded.loadUserOverlays('BTC-USD', '1d')).toEqual([{ kind: 'ema', period: 20 }])
  })

  it('bounds a bucket to MAX_PER_KEY overlays', () => {
    for (let p = 1; p <= 20; p++) mod.addUserOverlay('BTC-USD', '1d', { kind: 'ema', period: p })
    // Distinct overlayKeys (ema:1..ema:20) capped at 12, newest kept.
    const overlays = mod.loadUserOverlays('BTC-USD', '1d')
    expect(overlays).toHaveLength(12)
    expect(overlays[overlays.length - 1]).toEqual({ kind: 'ema', period: 20 })
  })
})
