/**
 * OverlayReconciler headless spec (Plan 0098 phase 2, ADR-0092) — migrated from the
 * deleted useOverlaySeries / useSupertrendSeries / useBbandsSeries / usePriceLines
 * hook specs. Exercises the reconcile against fake chart/series (no React), asserting
 * the add / reuse / remove discipline the component's overlay behaviour relies on.
 */
import type { IChartApi, IPriceLine, ISeriesApi } from 'lightweight-charts'

import { OverlayReconciler } from './overlayReconciler'
import type { MainSeries } from '../chartSeries'
import { overlayLayerId } from '../overlays'
import { priceLineId } from '../priceLines'
import type { Bar } from '../../types/sidecar/bar'
import type { OverlaySpec } from '../../types/events'

function fakeChart() {
  const added: Array<{ setData: jest.Mock; applyOptions: jest.Mock }> = []
  const removed: unknown[] = []
  const chart = {
    addSeries: () => {
      const s = { setData: jest.fn(), applyOptions: jest.fn() }
      added.push(s)
      return s as unknown as ISeriesApi<'Line'>
    },
    removeSeries: (s: unknown) => removed.push(s),
  } as unknown as IChartApi
  return { chart, added, removed }
}

function fakeMainSeries() {
  const created: Array<{ applyOptions: jest.Mock }> = []
  const removed: unknown[] = []
  const series = {
    createPriceLine: () => {
      const l = { applyOptions: jest.fn() }
      created.push(l)
      return l as unknown as IPriceLine
    },
    removePriceLine: (l: unknown) => removed.push(l),
  } as unknown as MainSeries
  return { series, created, removed }
}

const BARS: Bar[] = Array.from({ length: 60 }, (_, i) => ({
  event_ts: `2026-01-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 101 + i,
  low: 99 + i,
  close: 100 + i,
  volume: 1000,
})) as Bar[]

const container = () => document.createElement('div')
const EMA: OverlaySpec = { kind: 'ema', period: 20 } as OverlaySpec
const SUPERTREND: OverlaySpec = { kind: 'supertrend', period: 10, multiplier: 3 } as OverlaySpec
const BBANDS: OverlaySpec = { kind: 'bbands', period: 20, multiplier: 2 } as OverlaySpec
const PRICE_LINE: OverlaySpec = { kind: 'price_line', price: 120, label: 'R1' } as OverlaySpec

describe('OverlayReconciler — line-series families', () => {
  it('adds a line series for an ema overlay and sets its data', () => {
    const r = new OverlayReconciler()
    const { chart, added } = fakeChart()
    r.reconcile(chart, container(), {
      bars: BARS,
      overlays: [EMA],
      hidden: new Set(),
      theme: 'light',
    })
    expect(added).toHaveLength(1)
    expect(added[0].setData).toHaveBeenCalledTimes(1)
    expect(r.overlaySeriesRef.current.size).toBe(1)
  })

  it('removes the overlay series when its legend row is toggled off', () => {
    const r = new OverlayReconciler()
    const { chart, removed } = fakeChart()
    const c = container()
    r.reconcile(chart, c, { bars: BARS, overlays: [EMA], hidden: new Set(), theme: 'light' })
    expect(r.overlaySeriesRef.current.size).toBe(1)
    r.reconcile(chart, c, {
      bars: BARS,
      overlays: [EMA],
      hidden: new Set([overlayLayerId(EMA)]),
      theme: 'light',
    })
    expect(removed).toHaveLength(1)
    expect(r.overlaySeriesRef.current.size).toBe(0)
  })

  it('warns and draws nothing for an unsupported overlay kind', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    const r = new OverlayReconciler()
    const { chart, added } = fakeChart()
    r.reconcile(chart, container(), {
      bars: BARS,
      overlays: [{ kind: 'unregistered_test_kind', period: 14 } as unknown as OverlaySpec],
      hidden: new Set(),
      theme: 'light',
    })
    expect(added).toHaveLength(0)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('draws a supertrend overlay as two masked series, removed together on toggle-off', () => {
    const r = new OverlayReconciler()
    const { chart, added, removed } = fakeChart()
    const c = container()
    r.reconcile(chart, c, { bars: BARS, overlays: [SUPERTREND], hidden: new Set(), theme: 'light' })
    expect(added).toHaveLength(2)
    expect(r.supertrendSeriesRef.current.size).toBe(1)
    r.reconcile(chart, c, {
      bars: BARS,
      overlays: [SUPERTREND],
      hidden: new Set([overlayLayerId(SUPERTREND)]),
      theme: 'light',
    })
    expect(removed).toHaveLength(2)
    expect(r.supertrendSeriesRef.current.size).toBe(0)
  })

  it('draws a bbands overlay as a three-line triple, removed on toggle-off', () => {
    // NB: bbands is a registered overlay kind, so the generic line pass also creates
    // one (empty) series for it — faithful to the pre-fold useOverlaySeries +
    // useBbandsSeries pair. This asserts the triple specifically via its own map.
    const r = new OverlayReconciler()
    const { chart } = fakeChart()
    const c = container()
    r.reconcile(chart, c, { bars: BARS, overlays: [BBANDS], hidden: new Set(), theme: 'light' })
    expect(r.bbandsSeriesRef.current.size).toBe(1)
    const triple = r.bbandsSeriesRef.current.values().next().value
    expect(triple).toEqual(
      expect.objectContaining({
        upper: expect.anything(),
        middle: expect.anything(),
        lower: expect.anything(),
      }),
    )
    r.reconcile(chart, c, {
      bars: BARS,
      overlays: [BBANDS],
      hidden: new Set([overlayLayerId(BBANDS)]),
      theme: 'light',
    })
    expect(r.bbandsSeriesRef.current.size).toBe(0)
  })
})

describe('OverlayReconciler — price lines', () => {
  it('creates a horizontal price line and removes it on toggle-off', () => {
    const r = new OverlayReconciler()
    const { series, created, removed } = fakeMainSeries()
    const c = container()
    r.reconcilePriceLines(series, c, { overlays: [PRICE_LINE], hidden: new Set(), theme: 'light' })
    expect(created).toHaveLength(1)
    expect(r.priceLinesRef.current.size).toBe(1)
    r.reconcilePriceLines(series, c, {
      overlays: [PRICE_LINE],
      hidden: new Set([priceLineId(PRICE_LINE)]),
      theme: 'light',
    })
    expect(removed).toHaveLength(1)
    expect(r.priceLinesRef.current.size).toBe(0)
  })
})

const ANCHORED_VWAP: OverlaySpec = { kind: 'anchored_vwap' } as OverlaySpec
const PIVOTS: OverlaySpec = { kind: 'pivot_points', method: 'floor' } as OverlaySpec

describe('OverlayReconciler — anchored VWAP', () => {
  it('adds an anchored-VWAP line series and removes it on toggle-off', () => {
    const r = new OverlayReconciler()
    const { chart, added, removed } = fakeChart()
    r.reconcileAnchoredVwap(chart, { bars: BARS, overlays: [ANCHORED_VWAP], hidden: new Set() })
    expect(added).toHaveLength(1)
    expect(r.anchoredVwapRef.current.size).toBe(1)
    r.reconcileAnchoredVwap(chart, {
      bars: BARS,
      overlays: [ANCHORED_VWAP],
      hidden: new Set([overlayLayerId(ANCHORED_VWAP)]),
    })
    expect(removed).toHaveLength(1)
    expect(r.anchoredVwapRef.current.size).toBe(0)
  })
})

describe('OverlayReconciler — structure levels', () => {
  it('draws pivot price lines, returns the drawn levels, and clears them on toggle-off', () => {
    const r = new OverlayReconciler()
    const { series, created, removed } = fakeMainSeries()
    const levels = r.reconcileStructureLevels(series, {
      bars: BARS,
      overlays: [PIVOTS],
      hidden: new Set(),
    })
    expect(created.length).toBeGreaterThan(0)
    expect(levels.length).toBe(created.length)
    expect(r.structureLinesRef.current.size).toBe(created.length)

    const gone = r.reconcileStructureLevels(series, {
      bars: BARS,
      overlays: [PIVOTS],
      hidden: new Set([overlayLayerId(PIVOTS)]),
    })
    expect(gone).toHaveLength(0)
    expect(removed.length).toBe(created.length)
    expect(r.structureLinesRef.current.size).toBe(0)
  })
})
