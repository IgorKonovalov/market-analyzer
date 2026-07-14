/**
 * Plan 0105 phase 3 done-when: the OBV pane is RECONCILED like the oscillator
 * panes instead of created once and hidden — toggling OBV off removes its pane
 * (no empty ~30px band), toggling on re-creates it as the FIRST sub-pane with a
 * fresh divergence primitive attached, and an obv divergence keeps the pane
 * alive (series hidden) so its oscillator segment always has a pane to draw on.
 *
 * Drives the REAL component with a mocked `lightweight-charts` that captures the
 * `paneIndex` each series is placed on plus the `addPane`/`removePane`/`moveTo`
 * lifecycle, mirroring `CandlestickChart.oscillators.test.tsx`.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'
import type { Divergence } from '../types/events'

interface FakeSeries {
  _opts: Record<string, unknown>
  _paneIndex: number | undefined
  _type: string
  setData: jest.Mock
  applyOptions: jest.Mock
  attachPrimitive: jest.Mock
}

let allSeries: FakeSeries[] = []
let removedSeries: FakeSeries[] = []
let addPaneCount = 0
let removePaneCalls: number[] = []
let moveToCalls: number[] = []

function reset(): void {
  allSeries = []
  removedSeries = []
  addPaneCount = 0
  removePaneCalls = []
  moveToCalls = []
}

function makeSeries(type: string, opts: unknown, paneIndex: number | undefined): FakeSeries {
  const s = {
    _opts: (opts ?? {}) as Record<string, unknown>,
    _paneIndex: paneIndex,
    _type: type,
    setData: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
    setMarkers: jest.fn(),
    createPriceLine: jest.fn(() => ({ applyOptions: jest.fn() })),
    removePriceLine: jest.fn(),
  } as FakeSeries
  allSeries.push(s)
  return s
}

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => ({
    addSeries: jest.fn((def: { seriesType?: string }, opts: unknown, paneIndex?: number) =>
      makeSeries(def?.seriesType ?? 'Line', opts, paneIndex),
    ),
    addPane: jest.fn(() => {
      addPaneCount += 1
      return {
        setHeight: jest.fn(),
        getHeight: jest.fn(() => 0),
        moveTo: jest.fn((i: number) => moveToCalls.push(i)),
      }
    }),
    removePane: jest.fn((i: number) => removePaneCalls.push(i)),
    panes: jest.fn(() =>
      Array.from({ length: addPaneCount + 1 }, () => ({
        setHeight: jest.fn(),
        getHeight: jest.fn(() => 0),
        moveTo: jest.fn((i: number) => moveToCalls.push(i)),
      })),
    ),
    removeSeries: jest.fn((s: FakeSeries) => removedSeries.push(s)),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      getVisibleLogicalRange: jest.fn(() => null),
      setVisibleLogicalRange: jest.fn(),
      subscribeVisibleLogicalRangeChange: jest.fn(),
      unsubscribeVisibleLogicalRangeChange: jest.fn(),
    }),
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  })),
}))

const BARS: Bar[] = Array.from({ length: 30 }, (_, i) => ({
  symbol: 'BTC-USD',
  timeframe: '1d',
  event_ts: `2026-04-${String(i + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 102 + i + (i % 3),
  low: 98 + i - (i % 2),
  close: 100 + i + (i % 4),
  volume: 1_000_000,
  source: 'fixture',
}))

/** The live (never-removed) OBV line series — identified by its dedicated
 * per-pane price scale. */
function liveObvSeries(): FakeSeries[] {
  return allSeries.filter((s) => s._opts.priceScaleId === 'obv' && !removedSeries.includes(s))
}

const OBV_DIVERGENCE = {
  oscillator: 'obv',
  kind: 'regular_bearish',
  price_pivots: [],
  oscillator_pivots: [],
} as unknown as Divergence

beforeEach(reset)

describe('OBV pane lifecycle (Plan 0105 phase 3)', () => {
  it('creates the OBV pane + series as the first sub-pane when visible', () => {
    render(<CandlestickChart bars={BARS} />)
    const [obv] = liveObvSeries()
    expect(obv).toBeDefined()
    expect(obv._paneIndex).toBe(1)
    expect(obv.setData).toHaveBeenCalled()
    // Its divergence primitive is attached at (re)create.
    expect(obv.attachPrimitive).toHaveBeenCalledTimes(1)
    // Visible: the reconcile applies the current toggle state.
    expect(obv.applyOptions).toHaveBeenCalledWith({ visible: true })
  })

  it('toggling OBV off removes its series AND its pane; on re-creates both', () => {
    render(<CandlestickChart bars={BARS} />)
    const [obv] = liveObvSeries()

    fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
    expect(removedSeries).toContain(obv)
    expect(removePaneCalls).toContain(1)
    expect(liveObvSeries()).toHaveLength(0)

    fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
    const [recreated] = liveObvSeries()
    expect(recreated).toBeDefined()
    expect(recreated).not.toBe(obv)
    expect(recreated._paneIndex).toBe(1)
    expect(recreated.attachPrimitive).toHaveBeenCalledTimes(1)
  })

  it('keeps the pane (series hidden) when an obv divergence needs it', () => {
    render(<CandlestickChart bars={BARS} divergences={[OBV_DIVERGENCE]} />)
    const [obv] = liveObvSeries()

    fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
    // The pane survives for the divergence's oscillator segment...
    expect(removedSeries).not.toContain(obv)
    expect(removePaneCalls).toHaveLength(0)
    // ...but the OBV line itself is hidden.
    expect(obv.applyOptions).toHaveBeenCalledWith({ visible: false })
  })

  it('re-enabling OBV after an oscillator pane exists reclaims the first slot', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'cci' }]} />)
    const [obv] = liveObvSeries()
    expect(obv._paneIndex).toBe(1)

    fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
    expect(removedSeries).toContain(obv)

    fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
    const [recreated] = liveObvSeries()
    // ensure('obv', 0) moved the fresh pane to index 1, ahead of the CCI pane —
    // the pane-order invariant (OBV before oscillators) under lazy re-creation.
    expect(recreated._paneIndex).toBe(1)
    expect(moveToCalls).toContain(1)
  })

  it('a Clean chart (OBV toggled off, no divergence) has no OBV pane at all', () => {
    render(<CandlestickChart bars={BARS} />)
    fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
    expect(liveObvSeries()).toHaveLength(0)
    // The legend row still lists so it can be re-enabled.
    expect(screen.getByTestId('legend-row:series:obv')).toBeInTheDocument()
  })
})
