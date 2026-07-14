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
import type { ChartMarker } from '../lib/markers'
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
let lastMarkers: Array<{ color?: string; size?: number; position?: string; shape?: string }> = []

function reset(): void {
  lineSeries = []
  removedSeries = []
  createdPriceLines = []
  removedPriceLines = []
  lastMarkers = []
}

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => ({
    ...jest.requireActual('../tests/chartMockShared').paneStubs,
    addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
      candle: () => ({
        setData: jest.fn(),
        attachPrimitive: jest.fn(),
        detachPrimitive: jest.fn(),
        setMarkers: jest.fn((m: typeof lastMarkers) => {
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
      }),
      line: (opts: FakeLine['_opts']) => {
        const s: FakeLine = { _opts: opts, setData: jest.fn(), applyOptions: jest.fn() }
        lineSeries.push(s)
        return s
      },
      histogram: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
    }),
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

// A single sweep marker (candlestick identity per ADR-0045: pattern + direction).
const ANNOTATIONS: ChartMarker[] = [
  {
    event_ts: '2026-04-13T00:00:00+00:00',
    kind: 'bullish_marker',
    label: 'hammer',
    pattern: 'hammer',
  },
]

beforeEach(reset)

function renderChart() {
  return render(<CandlestickChart bars={BARS} overlays={OVERLAYS} annotations={ANNOTATIONS} />)
}

function row(id: string): HTMLElement {
  return screen.getByTestId(`legend-row:${id}`)
}
// The inline legend (Plan 0096 phase 2) replaced the LAYERS checklist: the
// swatch button is the visibility toggle, aria-pressed carries the on/off state.
function toggle(id: string): void {
  fireEvent.click(screen.getByTestId(`legend-toggle:${id}`))
}
function isVisible(id: string): boolean {
  return screen.getByTestId(`legend-toggle:${id}`).getAttribute('aria-pressed') === 'true'
}

it('renders one row per layer: overlays + OBV + candlestick master/group + price line', () => {
  renderChart()
  const legend = screen.getByTestId('chart-legend')
  const rows = within(legend).getAllByRole('listitem')
  // overlays ×2 + always-on OBV + candlestick master + (hammer, bullish) group + price line.
  expect(rows).toHaveLength(6)
  expect(screen.getByTestId('legend-row:overlay:ema:20')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:overlay:sma:50')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:series:obv')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:candles-master')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:candles:hammer|bullish_marker')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:pline:R1')).toBeInTheDocument()
})

// Plan 0076 phase 2 made the OBV row toggleable; Plan 0105 phase 3 made the
// toggle a pane-lifecycle event: off REMOVES the OBV series (+ its pane, so no
// empty band remains), on re-creates it. (No symbol → the legacy ephemeral,
// all-visible default, so OBV starts visible.)
it('toggling the OBV row removes and re-creates the OBV series', () => {
  renderChart()
  const obv = lineSeries.find((s) => s._opts.priceScaleId === 'obv')
  expect(obv).toBeDefined()

  toggle('series:obv')
  expect(removedSeries).toContain(obv)

  toggle('series:obv')
  const recreated = lineSeries.filter(
    (s) => s._opts.priceScaleId === 'obv' && !removedSeries.includes(s),
  )
  expect(recreated).toHaveLength(1)
  expect(recreated[0]).not.toBe(obv)
})

it('each swatch colour equals the colour the layer was drawn with', () => {
  renderChart()
  const [ema, sma] = overlayLines()
  // Overlay swatch === the line series colour.
  expect(screen.getByTestId('legend-swatch:overlay:ema:20')).toHaveStyle({
    backgroundColor: ema._opts.color as string,
  })
  expect(screen.getByTestId('legend-swatch:overlay:sma:50')).toHaveStyle({
    backgroundColor: sma._opts.color as string,
  })
  // Group swatch === the colour the group's markers were drawn with.
  expect(lastMarkers.length).toBeGreaterThan(0)
  expect(screen.getByTestId('legend-swatch:candles:hammer|bullish_marker')).toHaveStyle({
    backgroundColor: lastMarkers[0].color as string,
  })
  // Price-line swatch === the createPriceLine colour.
  expect(createdPriceLines).toHaveLength(1)
  expect(screen.getByTestId('legend-swatch:pline:R1')).toHaveStyle({
    backgroundColor: createdPriceLines[0].color,
  })
})

it('toggling an overlay off removes exactly that series; on re-adds it', () => {
  renderChart()
  const ema = overlayLines()[0]
  expect(removedSeries).not.toContain(ema)

  toggle('overlay:ema:20')
  expect(removedSeries).toContain(ema) // ema gone…
  // …and the sma series is untouched (still drawn).
  expect(removedSeries).not.toContain(overlayLines().find((s) => s._opts.color !== ema._opts.color))

  const beforeReadd = overlayLines().length
  toggle('overlay:ema:20')
  expect(overlayLines().length).toBe(beforeReadd + 1) // ema re-added
})

it('toggling the price line off removes it; on re-creates it', () => {
  renderChart()
  expect(createdPriceLines).toHaveLength(1)
  const first = createdPriceLines[0].line

  toggle('pline:R1')
  expect(removedPriceLines).toContain(first)

  toggle('pline:R1')
  expect(createdPriceLines).toHaveLength(2) // re-created
})

it('toggling the sole candlestick group off hides those markers (empty setMarkers)', () => {
  renderChart()
  // The single (hammer, bullish) group is the most-recent, so it draws by default.
  expect(lastMarkers.length).toBe(1)
  toggle('candles:hammer|bullish_marker')
  expect(lastMarkers.length).toBe(0)
})

it('toggle state is ephemeral (no symbol) — a remount restores all layers visible', () => {
  const { unmount } = renderChart()
  toggle('overlay:ema:20')
  expect(isVisible('overlay:ema:20')).toBe(false)

  unmount()
  reset()
  renderChart()
  // Fresh mount: overlays/price-lines back to visible.
  expect(isVisible('overlay:ema:20')).toBe(true)
  expect(isVisible('pline:R1')).toBe(true)
})

// ── Plan 0071 phase 2: grouped legend + draw-on-select for candlestick markers ──
//
// A dense sweep: 3 hammer (bullish) hits older, 2 doji (neutral) hits newest —
// two (pattern type, direction) groups over 5 markers. The doji group is the
// most-recent, so it draws by default and the wall of 5 never paints at once.
const SWEEP: ChartMarker[] = [
  { event_ts: '2026-04-10T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-11T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-12T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-20T00:00:00+00:00', kind: 'neutral_marker', pattern: 'doji' },
  { event_ts: '2026-04-21T00:00:00+00:00', kind: 'neutral_marker', pattern: 'doji' },
]

const HAMMER_ROW = 'candles:hammer|bullish_marker'
const DOJI_ROW = 'candles:doji|neutral_marker'

function renderSweep() {
  return render(<CandlestickChart bars={BARS} annotations={SWEEP} />)
}

it('does NOT paint all N markers at once — only the most-recent group draws', () => {
  renderSweep()
  // 5 markers arrived; the default draws only the 2 doji (most-recent) — never 5.
  expect(SWEEP).toHaveLength(5)
  expect(lastMarkers.length).toBe(2)
})

it('lists exactly one legend row per (type, direction) group with its count', () => {
  renderSweep()
  expect(screen.getByTestId('legend-row:candles-master')).toBeInTheDocument()
  expect(screen.getByTestId(`legend-count:${HAMMER_ROW}`)).toHaveTextContent('3')
  expect(screen.getByTestId(`legend-count:${DOJI_ROW}`)).toHaveTextContent('2')
  // Default selection: doji (most-recent) on, hammer off.
  expect(isVisible(DOJI_ROW)).toBe(true)
  expect(isVisible(HAMMER_ROW)).toBe(false)
})

it('toggling a group on draws exactly that group and off removes it', () => {
  renderSweep()
  expect(lastMarkers.length).toBe(2) // doji only
  // Enable hammer → its 3 markers add to the 2 doji (draws that group, nothing else).
  toggle(HAMMER_ROW)
  expect(lastMarkers.length).toBe(5)
  // Disable it again → back to the 2 doji.
  toggle(HAMMER_ROW)
  expect(lastMarkers.length).toBe(2)
})

it('the master toggle hides the whole layer without desyncing the per-group toggles', () => {
  renderSweep()
  expect(lastMarkers.length).toBe(2)
  // Master off → nothing draws, but the doji group stays selected.
  toggle('candles-master')
  expect(lastMarkers.length).toBe(0)
  expect(isVisible(DOJI_ROW)).toBe(true)
  // Master back on → the preserved doji selection redraws (no desync).
  toggle('candles-master')
  expect(lastMarkers.length).toBe(2)
})

it('hovering a group row emphasises that group and clears on leave', () => {
  renderSweep()
  const dojiSize = (): number => lastMarkers.find((m) => m.shape === 'circle')?.size ?? 0
  const before = dojiSize()
  expect(before).toBeGreaterThan(0)
  fireEvent.mouseEnter(row(DOJI_ROW))
  expect(dojiSize()).toBeGreaterThan(before) // hovered group's markers grow
  fireEvent.mouseLeave(row(DOJI_ROW))
  expect(dojiSize()).toBe(before) // emphasis cleared
})

it('still renders the empty/error states unchanged (no candlestick markers)', () => {
  render(<CandlestickChart bars={BARS} overlays={OVERLAYS} />)
  // No annotations → no candlestick master/group rows, but overlays + OBV + pline remain.
  expect(screen.queryByTestId('legend-row:candles-master')).toBeNull()
  expect(screen.getByTestId('legend-row:overlay:ema:20')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:series:obv')).toBeInTheDocument()
  expect(screen.getByTestId('legend-row:pline:R1')).toBeInTheDocument()
})
