/**
 * PrimitiveHub headless spec (Plan 0098 phase 3, ADR-0092) — migrated from the
 * deleted useChartMarkers hook spec + the useTrendlines / useIchimokuSeries /
 * useDivergences feed logic. Exercises attach + the feed methods against fake
 * series/chart (no React), spying the real primitives' feed calls.
 */
import { createSeriesMarkers } from 'lightweight-charts'
import type { IChartApi } from 'lightweight-charts'

import { PrimitiveHub } from './primitiveHub'
import { MARKET_STRUCTURE_LAYER_ID, chartColorsFrom, type MainSeries } from '../chartSeries'
import { resolveChartStyle } from '../chartStyle'
import { DivergencePrimitive } from '../divergences'
import { TrendlinePrimitive } from '../trendlines'
import type { Bar } from '../../types/sidecar/bar'
import type { Divergence, OverlaySpec, TrendlineSpec } from '../../types/events'
import type { OscillatorPaneEntry } from './oscillatorPanes'

const BARS: Bar[] = Array.from({ length: 60 }, (_, i) => ({
  event_ts: `2026-01-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 101 + i,
  low: 99 + i,
  close: 100 + i,
  volume: 1000,
})) as Bar[]

function fakeSeries(): MainSeries {
  return { attachPrimitive: jest.fn(), applyOptions: jest.fn() } as unknown as MainSeries
}
function container() {
  return document.createElement('div')
}
function colorsFor(c: HTMLDivElement) {
  return chartColorsFrom(resolveChartStyle(c, 'light'))
}
function markersPluginSetMarkers(): jest.Mock {
  return (createSeriesMarkers as jest.Mock).mock.results.at(-1)?.value.setMarkers
}

afterEach(() => jest.restoreAllMocks())

describe('PrimitiveHub — feed methods', () => {
  it('feeds the trendline primitive its specs + highlight', () => {
    const spy = jest.spyOn(TrendlinePrimitive.prototype, 'setTrendlines')
    const hub = new PrimitiveHub()
    const c = container()
    hub.attach(fakeSeries(), c, colorsFor(c))
    const specs = [] as ReadonlyArray<TrendlineSpec>
    hub.setTrendlines(c, specs, 'neckline:up')
    expect(spy).toHaveBeenCalledWith(specs)
  })

  it('feeds the price divergence primitive its divergences', () => {
    const spy = jest.spyOn(DivergencePrimitive.prototype, 'setDivergences')
    const hub = new PrimitiveHub()
    const c = container()
    hub.attach(fakeSeries(), c, colorsFor(c))
    const divs = [] as ReadonlyArray<Divergence>
    hub.setDivergences(c, divs, null, new Map<string, OscillatorPaneEntry>())
    expect(spy).toHaveBeenCalledWith(divs)
  })

  it('reserves trailing axis space for a visible Ichimoku cloud and resets it when gone', () => {
    const timeScale = { applyOptions: jest.fn() }
    const chart = { timeScale: () => timeScale } as unknown as IChartApi
    const hub = new PrimitiveHub()
    const c = container()
    hub.attach(fakeSeries(), c, colorsFor(c))

    const ichimoku = [{ kind: 'ichimoku' }] as unknown as OverlaySpec[]
    hub.setIchimoku(chart, c, BARS, ichimoku, new Set())
    expect(timeScale.applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ rightOffset: expect.any(Number) }),
    )
    const reserved = timeScale.applyOptions.mock.calls.at(-1)?.[0] as { rightOffset: number }
    expect(reserved.rightOffset).toBeGreaterThan(0)

    // Overlay removed → the reserved offset resets to 0.
    hub.setIchimoku(chart, c, BARS, [], new Set())
    expect(timeScale.applyOptions).toHaveBeenLastCalledWith({ rightOffset: 0 })
  })

  it('drives the candlestick markers plugin, adding the clicked-bar affordance', () => {
    const hub = new PrimitiveHub()
    const c = container()
    const series = fakeSeries()
    hub.attach(series, c, colorsFor(c))
    hub.setMarkers(series, c, {
      drawnMarkers: [],
      clickedBarTs: '2026-01-10T00:00:00+00:00',
      highlightGroup: null,
      theme: 'light',
    })
    const setMarkers = markersPluginSetMarkers()
    expect(setMarkers).toHaveBeenCalled()
    const drawn = setMarkers.mock.calls.at(-1)?.[0] as Array<{ shape: string }>
    expect(drawn).toHaveLength(1)
    expect(drawn[0].shape).toBe('circle')
  })

  it('recreates the markers plugin when the main series is rebuilt', () => {
    const hub = new PrimitiveHub()
    const c = container()
    const s1 = fakeSeries()
    hub.attach(s1, c, colorsFor(c))
    hub.setMarkers(s1, c, {
      drawnMarkers: [],
      clickedBarTs: null,
      highlightGroup: null,
      theme: 'light',
    })
    const firstCalls = (createSeriesMarkers as jest.Mock).mock.calls.length
    const s2 = fakeSeries()
    hub.attach(s2, c, colorsFor(c))
    hub.setMarkers(s2, c, {
      drawnMarkers: [],
      clickedBarTs: null,
      highlightGroup: null,
      theme: 'light',
    })
    expect((createSeriesMarkers as jest.Mock).mock.calls.length).toBeGreaterThan(firstCalls)
  })

  it('draws market-structure markers and returns the drawn points (empty when hidden)', () => {
    const hub = new PrimitiveHub()
    const c = container()
    const series = fakeSeries()
    hub.attach(series, c, colorsFor(c))
    const structure = {
      labeledPivots: [{ pivot: { ts: '2026-01-05T00:00:00+00:00', kind: 'high' }, label: 'HH' }],
      events: [],
    } as unknown as import('../marketStructure').MarketStructureResult

    const points = hub.setMarketStructure(series, c, {
      structure,
      bars: BARS,
      hidden: new Set(),
      theme: 'light',
    })
    expect(points).toEqual([{ time: expect.any(Number), label: 'HH' }])

    const hidden = hub.setMarketStructure(series, c, {
      structure,
      bars: BARS,
      hidden: new Set([MARKET_STRUCTURE_LAYER_ID]),
      theme: 'light',
    })
    expect(hidden).toEqual([])
  })
})
