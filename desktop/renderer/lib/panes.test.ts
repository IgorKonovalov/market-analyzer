/**
 * PaneRegistry unit tests (Plan 0095 phase 2) — against a stubbed IChartApi that
 * models v5 pane creation/removal + reindexing.
 */
import type { IChartApi } from 'lightweight-charts'

import { PaneRegistry } from './panes'

/** Minimal v5-chart stub: an array of panes (each carrying its id-less index),
 * with addPane appending, removePane splicing (v5 reindexes on removal), and
 * moveTo relocating the pane within the array (v5 reindexes on a move too). */
function fakeChart() {
  interface FakePane {
    setHeight: jest.Mock
    moveTo: jest.Mock
  }
  const panes: FakePane[] = []
  const makePane = (): FakePane => {
    const pane: FakePane = {
      setHeight: jest.fn(),
      moveTo: jest.fn((index: number) => {
        const from = panes.indexOf(pane)
        panes.splice(from, 1)
        panes.splice(index, 0, pane)
      }),
    }
    return pane
  }
  panes.push(makePane()) // pane 0 = price
  const addPane = jest.fn(() => {
    const p = makePane()
    panes.push(p)
    return p
  })
  const removePane = jest.fn((index: number) => {
    panes.splice(index, 1)
  })
  const chart = {
    addPane,
    removePane,
    panes: () => panes,
  } as unknown as IChartApi
  return { chart, panes, addPane, removePane }
}

describe('PaneRegistry', () => {
  it('creates a pane at basePane on first ensure and returns its index', () => {
    const { chart, addPane } = fakeChart()
    const reg = new PaneRegistry(chart)
    expect(reg.ensure('obv')).toBe(1)
    expect(addPane).toHaveBeenCalledTimes(1)
  })

  // Regression guard: panes MUST be created with preserveEmptyPane so removing a
  // pane's last series doesn't auto-free it out from under our explicit removePane
  // (`_cleanupIfPaneIsEmpty` → "Invalid pane index"). The mock can't model the
  // auto-cleanup, so we pin the flag at the creation boundary instead.
  it('creates panes with preserveEmptyPane (addPane(true))', () => {
    const { chart, addPane } = fakeChart()
    const reg = new PaneRegistry(chart)
    reg.ensure('obv')
    reg.ensure('cci')
    expect(addPane).toHaveBeenNthCalledWith(1, true)
    expect(addPane).toHaveBeenNthCalledWith(2, true)
  })

  it('reuses the same pane on a repeated ensure (no new pane)', () => {
    const { chart, addPane } = fakeChart()
    const reg = new PaneRegistry(chart)
    reg.ensure('obv')
    expect(reg.ensure('obv')).toBe(1)
    expect(addPane).toHaveBeenCalledTimes(1)
    expect(reg.has('obv')).toBe(true)
  })

  it('assigns consecutive indices to distinct ids', () => {
    const { chart } = fakeChart()
    const reg = new PaneRegistry(chart)
    expect(reg.ensure('stoch')).toBe(1)
    expect(reg.ensure('cci')).toBe(2)
    expect(reg.paneIndex('stoch')).toBe(1)
    expect(reg.paneIndex('cci')).toBe(2)
  })

  it('removes a pane and reindexes the ids below it', () => {
    const { chart, removePane, panes } = fakeChart()
    const reg = new PaneRegistry(chart)
    reg.ensure('stoch') // pane 1
    reg.ensure('cci') // pane 2
    reg.remove('stoch')
    expect(removePane).toHaveBeenCalledWith(1)
    // 'cci' shifts down from index 2 to index 1.
    expect(reg.has('stoch')).toBe(false)
    expect(reg.paneIndex('cci')).toBe(1)
    expect(panes).toHaveLength(2) // price pane + cci pane
  })

  it('remove is a no-op for an unknown id', () => {
    const { chart, removePane } = fakeChart()
    const reg = new PaneRegistry(chart)
    reg.remove('nope')
    expect(removePane).not.toHaveBeenCalled()
  })

  // Plan 0105 phase 3: the lazily re-created OBV pane claims slot 0 so it stays
  // the first sub-pane even when oscillator panes already exist.
  it('ensure(id, 0) inserts ahead of existing managed panes and reindexes them', () => {
    const { chart, panes } = fakeChart()
    const reg = new PaneRegistry(chart)
    reg.ensure('cci') // pane 1
    reg.ensure('mfi') // pane 2
    expect(reg.ensure('obv', 0)).toBe(1)
    // The new pane was moved to index 1; the oscillators shifted to 2..3.
    expect(reg.paneIndex('obv')).toBe(1)
    expect(reg.paneIndex('cci')).toBe(2)
    expect(reg.paneIndex('mfi')).toBe(3)
    expect(panes).toHaveLength(4) // price + obv + cci + mfi
    // The handle lookup follows the moved order.
    reg.pane('obv')?.setHeight(110)
    expect(panes[1].setHeight).toHaveBeenCalledWith(110)
  })

  it('ensure(id, at) appends when at points at/after the end, and never moves an existing pane', () => {
    const { chart, panes } = fakeChart()
    const reg = new PaneRegistry(chart)
    expect(reg.ensure('obv', 0)).toBe(1) // empty order -> plain append at basePane
    expect(reg.ensure('cci', 5)).toBe(2) // past the end -> append
    // Re-ensuring with a slot is a lookup, not a move.
    expect(reg.ensure('cci', 0)).toBe(2)
    expect(panes).toHaveLength(3)
  })

  it('exposes the pane handle for sizing, null when absent', () => {
    const { chart, panes } = fakeChart()
    const reg = new PaneRegistry(chart)
    reg.ensure('obv')
    reg.pane('obv')?.setHeight(110)
    expect(panes[1].setHeight as jest.Mock).toHaveBeenCalledWith(110)
    expect(reg.pane('missing')).toBeNull()
    expect(reg.paneIndex('missing')).toBeNull()
  })
})
