/**
 * Plan 0064 follow-up regression: the trendline primitive must ride the LIVE
 * chart under React StrictMode's dev double-invoke (mount → cleanup → mount).
 *
 * The original bug: `useTrendlines` attached the primitive once (guarded by a
 * "once" ref with no cleanup), so on the StrictMode remount it stayed attached
 * to the DISCARDED chart while the live chart had no primitive to paint —
 * trendlines never drew despite the specs arriving and segments computing. This
 * test creates a distinct chart+series per `createChart` call and asserts that
 * after StrictMode double-invokes, the surviving (live) chart's series is the
 * one carrying the trendline primitive, and that it has the specs (a pane view).
 *
 * This is the lifecycle the mocked-scale unit tests never exercised — they
 * called `currentSegments()` by hand and so were blind to "the library never
 * paints this primitive."
 */
import '@testing-library/jest-dom'
import { StrictMode } from 'react'
import { render } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { TrendlinePrimitive } from '../lib/trendlines'
import type { Bar } from '../types/sidecar/bar'
import type { TrendlineSpec } from '../types/events'

interface ChartRecord {
  trendlinePrimitive: TrendlinePrimitive | null
  removed: boolean
}

const charts: ChartRecord[] = []

jest.mock('lightweight-charts', () => {
  const makeTimeScale = () => ({
    fitContent: jest.fn(),
    getVisibleLogicalRange: jest.fn(() => null),
    setVisibleLogicalRange: jest.fn(),
    subscribeVisibleLogicalRangeChange: jest.fn(),
    unsubscribeVisibleLogicalRangeChange: jest.fn(),
    timeToCoordinate: jest.fn(() => 50),
    logicalToCoordinate: jest.fn((l: number) => l),
    getVisibleRange: jest.fn(() => null),
  })
  return {
    ...jest.requireActual('../tests/chartMockShared').seriesDefs,
    createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => {
      const record: ChartRecord = { trendlinePrimitive: null, removed: false }
      const timeScale = makeTimeScale()
      const series = {
        setData: jest.fn(),
        setMarkers: jest.fn(),
        applyOptions: jest.fn(),
        priceToCoordinate: jest.fn((p: number) => p),
        data: jest.fn(() => []),
        detachPrimitive: jest.fn(),
        attachPrimitive: (p: { attached?: (x: unknown) => void; setTrendlines?: unknown }) => {
          p.attached?.({ chart: api, series, requestUpdate: jest.fn() })
          // The trendline primitive is the one exposing `setTrendlines`.
          if (typeof p.setTrendlines === 'function') {
            record.trendlinePrimitive = p as unknown as TrendlinePrimitive
          }
        },
      }
      const api = {
        ...jest.requireActual('../tests/chartMockShared').paneStubs,
        addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
          candle: () => series,
          line: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
        }),
        priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
        removeSeries: jest.fn(),
        remove: jest.fn(() => {
          record.removed = true
        }),
        applyOptions: jest.fn(),
        timeScale: () => timeScale,
        subscribeClick: jest.fn(),
        unsubscribeClick: jest.fn(),
        subscribeCrosshairMove: jest.fn(),
        unsubscribeCrosshairMove: jest.fn(),
      }
      charts.push(record)
      return api
    }),
  }
})

const BARS: Bar[] = Array.from({ length: 3 }, (_, i) => ({
  symbol: 'BTC-USD',
  timeframe: '1d',
  event_ts: `2026-04-1${i + 3}T00:00:00+00:00`,
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1_000_000,
  source: 'fixture',
}))

const SPEC: TrendlineSpec = {
  points: [
    { ts: '2026-04-13T00:00:00+00:00', price: 100 },
    { ts: '2026-04-15T00:00:00+00:00', price: 104 },
  ],
  role: 'neckline',
  style: 'solid',
  pattern: 'head_shoulders',
}

beforeEach(() => {
  charts.length = 0
})

it('attaches the trendline primitive to the LIVE chart under StrictMode (regression)', () => {
  render(
    <StrictMode>
      <CandlestickChart bars={BARS} trendlines={[SPEC]} symbol="BTC-USD" timeframe="1d" />
    </StrictMode>,
  )

  // StrictMode double-invokes mount effects: chart #1 is created then discarded,
  // chart #2 is the live one. (If this environment ever stops double-invoking,
  // the guard below documents the assumption rather than passing vacuously.)
  expect(charts.length).toBeGreaterThan(1)

  const live = charts[charts.length - 1]
  const discarded = charts.slice(0, -1)

  // The discarded chart(s) were disposed; the live one was not.
  discarded.forEach((c) => expect(c.removed).toBe(true))
  expect(live.removed).toBe(false)

  // The LIVE chart must carry the trendline primitive, fed with the specs (so it
  // paints). Pre-fix, the primitive rode a discarded chart and the live chart's
  // primitive was null → no lines.
  expect(live.trendlinePrimitive).not.toBeNull()
  expect(live.trendlinePrimitive?.paneViews()).toHaveLength(1)
})
