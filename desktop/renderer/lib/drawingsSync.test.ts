/**
 * Plan 0104 phase 4: renderer→sidecar drawing sync (`lib/drawingsSync.ts`).
 *
 * Pins the done-when: a mutation triggers exactly one PUT of the FULL declarative
 * set plus exactly one `ui.drawing_changed` POST; a PUT failure retries once, logs,
 * and never throws (the local store is untouched). The typed client + ui-event
 * poster are mocked so no network is touched.
 */
import type { DrawingSpec } from '../types/events'

jest.mock('../api/client', () => ({ api: { putUserDrawings: jest.fn() } }))
jest.mock('../api/uiEvents', () => ({ postDrawingChanged: jest.fn() }))

type SyncModule = typeof import('./drawingsSync')
type StoreModule = typeof import('./userDrawings')
type ClientModule = typeof import('../api/client')
type UiEventsModule = typeof import('../api/uiEvents')

const hline = (id: string): DrawingSpec => ({
  kind: 'hline',
  points: [{ ts: '2026-05-01T00:00:00Z', price: 100 }],
  provenance: 'user',
  id,
})

async function fresh(): Promise<{
  sync: SyncModule
  store: StoreModule
  putMock: jest.Mock
  postMock: jest.Mock
}> {
  jest.resetModules()
  const client = (await import('../api/client')) as ClientModule
  const uiEvents = (await import('../api/uiEvents')) as UiEventsModule
  const store = (await import('./userDrawings')) as StoreModule
  const sync = (await import('./drawingsSync')) as SyncModule
  const putMock = client.api.putUserDrawings as jest.Mock
  const postMock = uiEvents.postDrawingChanged as jest.Mock
  putMock.mockResolvedValue({ symbol: 'AAPL', drawing_count: 0, synced_at: null })
  postMock.mockResolvedValue(undefined)
  return { sync, store, putMock, postMock }
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('syncUserDrawings', () => {
  it('PUTs the full current user set for the symbol exactly once', async () => {
    const { sync, store, putMock } = await fresh()
    store.addUserDrawing('AAPL', hline('a'))
    store.addUserDrawing('AAPL', hline('b'))
    await sync.syncUserDrawings('AAPL')
    expect(putMock).toHaveBeenCalledTimes(1)
    const [symbol, drawings] = putMock.mock.calls[0]
    expect(symbol).toBe('AAPL')
    expect(drawings.map((d: DrawingSpec) => d.id)).toEqual(['a', 'b'])
  })

  it('retries once, logs, and does not throw on a persistent failure', async () => {
    const { sync, store, putMock } = await fresh()
    store.addUserDrawing('AAPL', hline('a'))
    putMock.mockRejectedValue(new Error('offline'))
    const debug = jest.spyOn(console, 'debug').mockImplementation(() => {})

    await expect(sync.syncUserDrawings('AAPL')).resolves.toBeUndefined()
    expect(putMock).toHaveBeenCalledTimes(2) // initial + one retry
    expect(debug).toHaveBeenCalled()
    // The local store is untouched by a sync failure (the mirror is a shadow).
    expect(store.loadUserDrawings('AAPL').map((d) => d.id)).toEqual(['a'])
    debug.mockRestore()
  })
})

describe('notifyDrawingMutation', () => {
  it('emits exactly one PUT and one ui.drawing_changed per mutation', async () => {
    const { sync, store, putMock, postMock } = await fresh()
    store.addUserDrawing('AAPL', hline('a'))
    sync.notifyDrawingMutation('AAPL', 'created', 'a', 'hline')
    await Promise.resolve() // let the fire-and-forget PUT dispatch
    expect(putMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith({
      symbol: 'AAPL',
      change: 'created',
      drawing_id: 'a',
      kind: 'hline',
    })
  })
})
