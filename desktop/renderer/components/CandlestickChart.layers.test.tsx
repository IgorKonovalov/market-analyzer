/**
 * Plan 0047 phase 9 done-when: the chart layers legend.
 *
 * Drives the REAL component with a mocked `lightweight-charts` that records the
 * line series + price lines it draws, so we can assert: one panel row per layer
 * (overlays + marker group + price line); each swatch colour equals the colour
 * the layer is drawn with; unchecking a row removes exactly that layer (series /
 * price line) and re-checking re-adds it; and the toggle state resets on remount.
 *
 * No sidecar/IPC: the mock makes no network call; the legend is pure renderer
 * state driven by props.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Annotation } from '../types/sidecar/annotation'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

interface FakeLine {
  _opts: { color?: string; priceScaleId?: string } & Record<string, unknown>
  setData: jest.Mock
  applyOptions: jest.Mock
}
interface FakePriceLine {
  applyOptions: jest.Mock
}

let lineSeries: FakeLine[] = []
let removedSeries: FakeLine[] = []
let createdPriceLines: Array<{ price: number; color: string; line: FakePriceLine }> = []
let removedPriceLines: FakePriceLine[] = []
let lastMarkers: Array<{ color?: string }> = []

function reset(): void {
  lineSeries = []
  removedSeries = []
  createdPriceLines = []
  removedPriceLines = []
  lastMarkers = []
}

jest.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => ({
    addCandlestickSeries: jest.fn(() => ({
      setData: jest.fn(),
      attachPrimitive: jest.fn(),
      detachPrimitive: jest.fn(),
      setMarkers: jest.fn((m: Array<{ color?: string }>) => {
        lastMarkers = m
      }),
      applyOptions: jest.fn(),
      createPriceLine: jest.fn((opts: { price: number; color: string }) => {
        const line: FakePriceLine = { applyOptions: jest.fn() }
        createdPriceLines.push({ price: opts.price, color: opts.color, line })
        return line
      }),
      removePriceLine: jest.fn((line: FakePriceLine) => {
        removedPriceLines.push(line)
      }),
    })),
    addLineSeries: jest.fn((opts: FakeLine['_opts']) => {
      const s: FakeLine = { _opts: opts, setData: jest.fn(), applyOptions: jest.fn() }
      lineSeries.push(s)
      return s
    }),
    addHistogramSeries: jest.fn(() => ({ setData: jest.fn(), applyOptions: jest.fn() })),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn((s: FakeLine) => {
      removedSeries.push(s)
    }),
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

// The agent overlay line series are the ones with no explicit priceScaleId (the
// always-on volume/VWAP/OBV lines all pin one).
function overlayLines(): FakeLine[] {
  return lineSeries.filter((s) => s._opts.priceScaleId === undefined)
}

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

const OVERLAYS: OverlaySpec[] = [
  { kind: 'ema', period: 20 },
  { kind: 'sma', period: 50 },
  { kind: 'price_line', price: 61_335.75, label: 'R1', role: 'resistance' },
]

const ANNOTATIONS: Annotation[] = [
  {
    id: 'ann-1',
    symbol: 'BTC-USD',
    timeframe: '1d',
    event_ts: '2026-04-13T00:00:00+00:00',
    kind: 'bullish_marker',
    label: 'hammer',
    agent_id: 'test',
    created_at: '2026-04-13T01:00:00+00:00',
  },
]

beforeEach(reset)

function renderChart() {
  return render(<CandlestickChart bars={BARS} overlays={OVERLAYS} annotations={ANNOTATIONS} />)
}

function row(id: string): HTMLElement {
  return screen.getByTestId(`layer-row:${id}`)
}

it('renders one row per layer: overlays + marker group + price line', () => {
  renderChart()
  const panel = screen.getByTestId('layers-panel')
  const rows = within(panel).getAllByRole('listitem')
  expect(rows).toHaveLength(4)
  expect(screen.getByTestId('layer-row:overlay:ema:20')).toBeInTheDocument()
  expect(screen.getByTestId('layer-row:overlay:sma:50')).toBeInTheDocument()
  expect(screen.getByTestId('layer-row:marker:bullish')).toBeInTheDocument()
  expect(screen.getByTestId('layer-row:pline:R1')).toBeInTheDocument()
})

it('each swatch colour equals the colour the layer was drawn with', () => {
  renderChart()
  const [ema, sma] = overlayLines()
  // Overlay swatch === the line series colour.
  expect(screen.getByTestId('layer-swatch:overlay:ema:20')).toHaveStyle({
    backgroundColor: ema._opts.color as string,
  })
  expect(screen.getByTestId('layer-swatch:overlay:sma:50')).toHaveStyle({
    backgroundColor: sma._opts.color as string,
  })
  // Marker swatch === the colour the markers were drawn with.
  expect(lastMarkers.length).toBeGreaterThan(0)
  expect(screen.getByTestId('layer-swatch:marker:bullish')).toHaveStyle({
    backgroundColor: lastMarkers[0].color as string,
  })
  // Price-line swatch === the createPriceLine colour.
  expect(createdPriceLines).toHaveLength(1)
  expect(screen.getByTestId('layer-swatch:pline:R1')).toHaveStyle({
    backgroundColor: createdPriceLines[0].color,
  })
})

it('unchecking an overlay removes exactly that series; re-checking re-adds it', () => {
  renderChart()
  const ema = overlayLines()[0]
  expect(removedSeries).not.toContain(ema)

  fireEvent.click(within(row('overlay:ema:20')).getByRole('checkbox'))
  expect(removedSeries).toContain(ema) // ema gone…
  // …and the sma series is untouched (still drawn).
  expect(removedSeries).not.toContain(overlayLines().find((s) => s._opts.color !== ema._opts.color))

  const beforeReadd = overlayLines().length
  fireEvent.click(within(row('overlay:ema:20')).getByRole('checkbox'))
  expect(overlayLines().length).toBe(beforeReadd + 1) // ema re-added
})

it('unchecking the price line removes it; re-checking re-creates it', () => {
  renderChart()
  expect(createdPriceLines).toHaveLength(1)
  const first = createdPriceLines[0].line

  fireEvent.click(within(row('pline:R1')).getByRole('checkbox'))
  expect(removedPriceLines).toContain(first)

  fireEvent.click(within(row('pline:R1')).getByRole('checkbox'))
  expect(createdPriceLines).toHaveLength(2) // re-created
})

it('unchecking the marker group hides those markers (empty setMarkers)', () => {
  renderChart()
  expect(lastMarkers.length).toBe(1)
  fireEvent.click(within(row('marker:bullish')).getByRole('checkbox'))
  expect(lastMarkers.length).toBe(0)
})

it('toggle state is ephemeral — a remount restores all layers visible', () => {
  const { unmount } = renderChart()
  fireEvent.click(within(row('overlay:ema:20')).getByRole('checkbox'))
  expect(within(row('overlay:ema:20')).getByRole('checkbox')).not.toBeChecked()

  unmount()
  reset()
  renderChart()
  // Fresh mount: nothing hidden.
  expect(within(row('overlay:ema:20')).getByRole('checkbox')).toBeChecked()
  expect(within(row('pline:R1')).getByRole('checkbox')).toBeChecked()
})
