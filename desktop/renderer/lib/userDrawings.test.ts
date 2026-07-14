/**
 * Plan 0097 phase 2: the per-symbol user-drawing store (`lib/userDrawings.ts`).
 *
 * Pins the done-when persistence claims — place → persist → reload restores; a
 * drag (updateUserDrawing) rewrites the stored anchor; per-symbol isolation; and
 * the sanitize/bounds guards that keep a malformed or unbounded persisted record
 * from being drawn. The store hydrates from `localStorage` at module load, so
 * "reload" is modelled by `jest.resetModules()` + a fresh dynamic import reading
 * the same jsdom `localStorage`.
 */
import type { DrawingSpec, TimePricePoint } from '../types/events'

type StoreModule = typeof import('./userDrawings')

const tp = (ts: string, price: number): TimePricePoint => ({ ts, price })

function drawing(id: string, points?: TimePricePoint[]): DrawingSpec {
  return {
    kind: 'trendline',
    points: points ?? [tp('2026-05-01T00:00:00Z', 100), tp('2026-05-05T00:00:00Z', 110)],
    provenance: 'user',
    id,
  }
}

async function freshStore(): Promise<StoreModule> {
  jest.resetModules()
  return import('./userDrawings')
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('userDrawings store', () => {
  it('persists a drawing and restores it on reload', async () => {
    const m1 = await freshStore()
    m1.addUserDrawing('AAPL', drawing('d1'))
    expect(m1.loadUserDrawings('AAPL').map((d) => d.id)).toEqual(['d1'])

    // Reload: a fresh module instance rehydrates from the same localStorage.
    const m2 = await freshStore()
    const restored = m2.loadUserDrawings('AAPL')
    expect(restored).toHaveLength(1)
    expect(restored[0].id).toBe('d1')
    expect(restored[0].points[1]).toEqual(tp('2026-05-05T00:00:00Z', 110))
    expect(restored[0].provenance).toBe('user')
  })

  it('updateUserDrawing rewrites the stored anchor (the drag path)', async () => {
    const m = await freshStore()
    m.addUserDrawing('AAPL', drawing('d1'))
    m.updateUserDrawing(
      'AAPL',
      drawing('d1', [tp('2026-05-01T00:00:00Z', 100), tp('2026-05-06T00:00:00Z', 125)]),
    )
    expect(m.loadUserDrawings('AAPL')[0].points[1]).toEqual(tp('2026-05-06T00:00:00Z', 125))
  })

  it('updateUserDrawing never resurrects a drawing that is not present', async () => {
    const m = await freshStore()
    m.updateUserDrawing('AAPL', drawing('ghost'))
    expect(m.loadUserDrawings('AAPL')).toHaveLength(0)
  })

  it('removeUserDrawing drops it and empties the bucket', async () => {
    const m = await freshStore()
    m.addUserDrawing('AAPL', drawing('d1'))
    m.removeUserDrawing('AAPL', 'd1')
    expect(m.loadUserDrawings('AAPL')).toHaveLength(0)
    // Bucket dropped, not left empty.
    expect(Object.keys(m.getUserDrawingsSnapshot())).not.toContain('AAPL')
  })

  it('isolates drawings per symbol', async () => {
    const m = await freshStore()
    m.addUserDrawing('AAPL', drawing('a1'))
    m.addUserDrawing('MSFT', drawing('m1'))
    expect(m.loadUserDrawings('AAPL').map((d) => d.id)).toEqual(['a1'])
    expect(m.loadUserDrawings('MSFT').map((d) => d.id)).toEqual(['m1'])
  })

  it('replaces (does not duplicate) a drawing re-added with the same id', async () => {
    const m = await freshStore()
    m.addUserDrawing('AAPL', drawing('d1'))
    m.addUserDrawing(
      'AAPL',
      drawing('d1', [tp('2026-05-01T00:00:00Z', 90), tp('2026-05-05T00:00:00Z', 95)]),
    )
    const list = m.loadUserDrawings('AAPL')
    expect(list).toHaveLength(1)
    expect(list[0].points[0]).toEqual(tp('2026-05-01T00:00:00Z', 90))
  })

  it('notifies subscribers on mutation', async () => {
    const m = await freshStore()
    const cb = jest.fn()
    const unsub = m.subscribeUserDrawings(cb)
    m.addUserDrawing('AAPL', drawing('d1'))
    expect(cb).toHaveBeenCalledTimes(1)
    unsub()
    m.addUserDrawing('AAPL', drawing('d2'))
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('drops a persisted record with the wrong anchor count on load', async () => {
    // A trendline needs exactly 2 points; a 1-point record is malformed.
    window.localStorage.setItem(
      'ma.userDrawings',
      JSON.stringify({
        AAPL: [
          {
            kind: 'trendline',
            points: [tp('2026-05-01T00:00:00Z', 100)],
            provenance: 'user',
            id: 'bad',
          },
        ],
      }),
    )
    const m = await freshStore()
    expect(m.loadUserDrawings('AAPL')).toHaveLength(0)
  })

  it('round-trips a single-anchor kind (hline) through persistence', async () => {
    const hline: DrawingSpec = {
      kind: 'hline',
      points: [tp('2026-05-02T00:00:00Z', 118)],
      provenance: 'user',
      id: 'h1',
    }
    const m1 = await freshStore()
    m1.addUserDrawing('AAPL', hline)
    const m2 = await freshStore()
    const restored = m2.loadUserDrawings('AAPL')
    expect(restored).toHaveLength(1)
    expect(restored[0].kind).toBe('hline')
    expect(restored[0].points).toEqual([tp('2026-05-02T00:00:00Z', 118)])
  })

  it('drops a two-anchor kind (rect) persisted with only one point', async () => {
    window.localStorage.setItem(
      'ma.userDrawings',
      JSON.stringify({
        AAPL: [
          {
            kind: 'rect',
            points: [tp('2026-05-01T00:00:00Z', 100)],
            provenance: 'user',
            id: 'bad',
          },
        ],
      }),
    )
    const m = await freshStore()
    expect(m.loadUserDrawings('AAPL')).toHaveLength(0)
  })

  it('forces stored provenance to user even if the record claims agent', async () => {
    window.localStorage.setItem(
      'ma.userDrawings',
      JSON.stringify({ AAPL: [{ ...drawing('d1'), provenance: 'agent' }] }),
    )
    const m = await freshStore()
    const list = m.loadUserDrawings('AAPL')
    expect(list).toHaveLength(1)
    expect(list[0].provenance).toBe('user')
  })
})
