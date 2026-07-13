/**
 * Plan 0091 phase 6 done-when: oscillator overlays draw in their OWN v5 sub-panes
 * (via `addPane()` / `addSeries(def, opts, paneIndex)`), and a LayersPanel toggle
 * shows/hides each pane.
 *
 * Drives the REAL component with a mocked `lightweight-charts` that captures the
 * `paneIndex` each series is placed on plus the `addPane`/`removePane` lifecycle,
 * so we can assert: a stochastic overlay creates its own pane with two lines
 * (%K/%D); a single-line oscillator (CCI) creates one; toggling the legend row off
 * removes the pane + its series; toggling back on re-creates them.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'

interface FakeSeries {
  _opts: Record<string, unknown>
  _paneIndex: number | undefined
  _type: string
  setData: jest.Mock
  applyOptions: jest.Mock
}

let allSeries: FakeSeries[] = []
let removedSeries: FakeSeries[] = []
let addPaneCount = 0
let removePaneCalls: number[] = []

function reset(): void {
  allSeries = []
  removedSeries = []
  addPaneCount = 0
  removePaneCalls = []
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
      return { setHeight: jest.fn(), getHeight: jest.fn(() => 0) }
    }),
    removePane: jest.fn((i: number) => removePaneCalls.push(i)),
    panes: jest.fn(() =>
      Array.from({ length: addPaneCount + 1 }, () => ({
        setHeight: jest.fn(),
        getHeight: jest.fn(() => 0),
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

// A rising/varying 30-bar series so the oscillators are well-defined at defaults.
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

/** Line series drawn on an oscillator pane (paneIndex >= 2, i.e. below the price
 * pane and the OBV pane at index 1). */
function oscillatorPaneSeries(): FakeSeries[] {
  return allSeries.filter((s) => s._type === 'Line' && (s._paneIndex ?? 0) >= 2)
}

beforeEach(reset)

describe('oscillator sub-panes (Plan 0091 phase 6)', () => {
  it('draws a Stochastic overlay as two lines on its own pane', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'stochastic' }]} />)
    // Its own pane was created (beyond price pane 0 + OBV pane 1).
    expect(addPaneCount).toBeGreaterThanOrEqual(2)
    // %K + %D — two lines on the oscillator pane.
    expect(oscillatorPaneSeries()).toHaveLength(2)
    // The mirrored data was pushed (non-empty at 30 bars).
    for (const s of oscillatorPaneSeries()) {
      expect(s.setData).toHaveBeenCalled()
    }
    // A toggleable legend row exists for it.
    expect(screen.getByTestId('legend-row:overlay:stochastic:na')).toBeInTheDocument()
  })

  it('draws a single-line oscillator (CCI) on its own pane', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'cci' }]} />)
    expect(oscillatorPaneSeries()).toHaveLength(1)
    expect(screen.getByTestId('legend-row:overlay:cci:na')).toBeInTheDocument()
  })

  it('toggling the legend row off removes the oscillator pane + its series', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'stochastic' }]} />)
    const drawn = oscillatorPaneSeries()
    expect(drawn).toHaveLength(2)

    fireEvent.click(screen.getByTestId('legend-toggle:overlay:stochastic:na'))
    // Both pane series removed, and the pane itself torn down.
    for (const s of drawn) expect(removedSeries).toContain(s)
    expect(removePaneCalls.length).toBeGreaterThanOrEqual(1)

    // Re-checking re-creates the pane + its two lines.
    fireEvent.click(screen.getByTestId('legend-toggle:overlay:stochastic:na'))
    expect(oscillatorPaneSeries().filter((s) => !removedSeries.includes(s))).toHaveLength(2)
  })

  it.each(['mfi', 'cmf', 'ad_line'] as const)(
    'draws money-flow %s on its own pane with a legend row',
    (kind) => {
      render(<CandlestickChart bars={BARS} overlays={[{ kind }]} />)
      expect(oscillatorPaneSeries()).toHaveLength(1)
      expect(screen.getByTestId(`legend-row:overlay:${kind}:na`)).toBeInTheDocument()
    },
  )

  it('does not draw an oscillator on the price pane (no double-draw)', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'roc' }]} />)
    // The ROC line sits on its own pane, never the price pane (paneIndex 0/undefined
    // with no priceScaleId — the generic overlay path skips oscillators).
    const pricePaneLines = allSeries.filter(
      (s) => s._type === 'Line' && (s._paneIndex ?? 0) === 0 && s._opts.priceScaleId === undefined,
    )
    expect(pricePaneLines).toHaveLength(0)
    expect(oscillatorPaneSeries()).toHaveLength(1)
  })
})
