/**
 * OscillatorPaneReconciler headless spec (Plan 0098 phase 2, ADR-0092). Exercises
 * the pane create / reuse / teardown discipline against a fake chart + PaneRegistry
 * (no React) — the reusable-wrapper contract Plan 0091 phase 6 introduced, now owned
 * by the controller: one real pane per active oscillator, torn down on toggle-off,
 * ensured for a divergence's `requiredKinds` even when the overlay is absent.
 */
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { OscillatorPaneReconciler, oscillatorPaneId } from './oscillatorPanes'
import type { PaneRegistry } from '../panes'
import { overlayLayerId } from '../overlays'
import type { Bar } from '../../types/sidecar/bar'
import type { OverlayKind, OverlaySpec } from '../../types/events'

function fakeChart() {
  const added: Array<{ setData: jest.Mock; attachPrimitive: jest.Mock }> = []
  const removed: unknown[] = []
  const chart = {
    addSeries: () => {
      const s = { setData: jest.fn(), attachPrimitive: jest.fn() }
      added.push(s)
      return s as unknown as ISeriesApi<'Line'>
    },
    removeSeries: (s: unknown) => removed.push(s),
  } as unknown as IChartApi
  return { chart, added, removed }
}

function fakeRegistry() {
  const ensured: string[] = []
  const removed: string[] = []
  let idx = 1
  const registry = {
    ensure: (id: string) => {
      ensured.push(id)
      return idx++
    },
    pane: () => ({ setHeight: jest.fn() }),
    remove: (id: string) => removed.push(id),
  } as unknown as PaneRegistry
  return { registry, ensured, removed }
}

const BARS: Bar[] = Array.from({ length: 60 }, (_, i) => ({
  event_ts: `2026-01-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 101 + i,
  low: 99 + i,
  close: 100 + i,
  volume: 1000,
})) as Bar[]

const RSI: OverlaySpec = { kind: 'rsi' } as OverlaySpec
const STOCH: OverlaySpec = { kind: 'stochastic' } as OverlaySpec
const NONE: ReadonlySet<OverlayKind> = new Set()

describe('OscillatorPaneReconciler', () => {
  it('creates a pane + series for an active oscillator and sets its data', () => {
    const r = new OscillatorPaneReconciler()
    const { chart, added } = fakeChart()
    const { registry, ensured } = fakeRegistry()
    r.reconcile(chart, registry, {
      bars: BARS,
      overlays: [RSI],
      hidden: new Set(),
      requiredKinds: NONE,
    })
    expect(ensured).toEqual([oscillatorPaneId('rsi')])
    expect(added).toHaveLength(1)
    expect(added[0].setData).toHaveBeenCalled()
    expect(r.panesRef.current.size).toBe(1)
  })

  it('tears the pane down when the oscillator is toggled off', () => {
    const r = new OscillatorPaneReconciler()
    const { chart, removed: seriesRemoved } = fakeChart()
    const { registry, removed: panesRemoved } = fakeRegistry()
    r.reconcile(chart, registry, {
      bars: BARS,
      overlays: [RSI],
      hidden: new Set(),
      requiredKinds: NONE,
    })
    r.reconcile(chart, registry, {
      bars: BARS,
      overlays: [RSI],
      hidden: new Set([overlayLayerId(RSI)]),
      requiredKinds: NONE,
    })
    expect(seriesRemoved).toHaveLength(1)
    expect(panesRemoved).toEqual([oscillatorPaneId('rsi')])
    expect(r.panesRef.current.size).toBe(0)
  })

  it('ensures a pane for a divergence-required oscillator even with no overlay', () => {
    const r = new OscillatorPaneReconciler()
    const { chart } = fakeChart()
    const { registry, ensured } = fakeRegistry()
    r.reconcile(chart, registry, {
      bars: BARS,
      overlays: [],
      hidden: new Set(),
      requiredKinds: new Set<OverlayKind>(['rsi']),
    })
    expect(ensured).toEqual([oscillatorPaneId('rsi')])
    expect(r.panesRef.current.size).toBe(1)
  })

  it('draws the stochastic pane as two lines (%K, %D)', () => {
    const r = new OscillatorPaneReconciler()
    const { chart, added } = fakeChart()
    const { registry } = fakeRegistry()
    r.reconcile(chart, registry, {
      bars: BARS,
      overlays: [STOCH],
      hidden: new Set(),
      requiredKinds: NONE,
    })
    expect(added).toHaveLength(2)
  })
})
