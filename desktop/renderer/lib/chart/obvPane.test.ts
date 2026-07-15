/**
 * ObvPaneReconciler headless spec (Plan 0098 thin-A) — migrated from the OBV-pane
 * behaviour formerly covered through the component. Exercises lazy create / remove /
 * divergence-keeps-pane against a fake chart + PaneRegistry (no React).
 */
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { ObvPaneReconciler } from './obvPane'
import { OBV_LAYER_ID } from '../chartSeries'
import type { PaneRegistry } from '../panes'
import type { Bar } from '../../types/sidecar/bar'
import type { Divergence } from '../../types/events'

function fakeChart() {
  const removed: unknown[] = []
  const chart = {
    addSeries: () =>
      ({
        setData: jest.fn(),
        applyOptions: jest.fn(),
        attachPrimitive: jest.fn(),
      }) as unknown as ISeriesApi<'Line'>,
    removeSeries: (s: unknown) => removed.push(s),
  } as unknown as IChartApi
  return { chart, removed }
}

function fakeRegistry() {
  const removed: string[] = []
  const registry = {
    ensure: () => 1,
    pane: () => ({ setHeight: jest.fn() }),
    remove: (id: string) => removed.push(id),
  } as unknown as PaneRegistry
  return { registry, removed }
}

const BARS: Bar[] = Array.from({ length: 30 }, (_, i) => ({
  event_ts: `2026-01-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 101 + i,
  low: 99 + i,
  close: 100 + i,
  volume: 1000,
})) as Bar[]

const OBV_DIVERGENCE = [{ oscillator: 'obv' }] as unknown as Divergence[]
const container = () => document.createElement('div')

describe('ObvPaneReconciler', () => {
  it('lazily creates the pane + series when the OBV row is visible', () => {
    const r = new ObvPaneReconciler()
    const { chart } = fakeChart()
    const { registry } = fakeRegistry()
    r.reconcile(chart, container(), registry, {
      bars: BARS,
      hidden: new Set(),
      divergences: [],
      theme: 'light',
    })
    expect(r.seriesRef.current).not.toBeNull()
    expect(r.divergencePrimitiveRef.current).not.toBeNull()
  })

  it('removes the pane when the OBV row is toggled off and no divergence needs it', () => {
    const r = new ObvPaneReconciler()
    const { chart, removed } = fakeChart()
    const { registry, removed: paneRemoved } = fakeRegistry()
    const c = container()
    r.reconcile(chart, c, registry, {
      bars: BARS,
      hidden: new Set(),
      divergences: [],
      theme: 'light',
    })
    r.reconcile(chart, c, registry, {
      bars: BARS,
      hidden: new Set([OBV_LAYER_ID]),
      divergences: [],
      theme: 'light',
    })
    expect(removed).toHaveLength(1)
    expect(paneRemoved).toHaveLength(1)
    expect(r.seriesRef.current).toBeNull()
  })

  it('keeps the pane alive (line hidden) when an obv divergence needs it', () => {
    const r = new ObvPaneReconciler()
    const { chart, removed } = fakeChart()
    const { registry } = fakeRegistry()
    const c = container()
    r.reconcile(chart, c, registry, {
      bars: BARS,
      hidden: new Set(),
      divergences: [],
      theme: 'light',
    })
    const series = r.seriesRef.current as unknown as { applyOptions: jest.Mock }
    r.reconcile(chart, c, registry, {
      bars: BARS,
      hidden: new Set([OBV_LAYER_ID]),
      divergences: OBV_DIVERGENCE,
      theme: 'light',
    })
    expect(removed).toHaveLength(0)
    expect(r.seriesRef.current).not.toBeNull()
    expect(series.applyOptions).toHaveBeenLastCalledWith({ visible: false })
  })
})
