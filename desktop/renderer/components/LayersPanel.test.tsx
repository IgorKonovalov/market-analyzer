/**
 * Plan 0047 phase 9: the LayersPanel presentational component in isolation —
 * row rendering, the empty case, swatch colour, and the toggle callback. The
 * chart-integration behaviour (show/hide a drawn layer) lives in
 * CandlestickChart.layers.test.tsx.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { LayersPanel, type ChartLayer } from './LayersPanel'

beforeEach(() => localStorage.clear())

const LAYERS: ChartLayer[] = [
  { id: 'overlay:ema:20', label: 'EMA(20)', color: '#2563eb', kind: 'overlay', visible: true },
  {
    id: 'marker:bullish',
    label: 'Bullish markers',
    color: '#16a34a',
    kind: 'marker',
    visible: false,
  },
  { id: 'pline:R1', label: 'R1 (61335.75)', color: '#dc2626', kind: 'price_line', visible: true },
]

it('renders nothing when there are no layers', () => {
  const { container } = render(<LayersPanel layers={[]} onToggle={() => {}} />)
  expect(container).toBeEmptyDOMElement()
})

it('renders one row per layer with its label, swatch colour, and checked state', () => {
  render(<LayersPanel layers={LAYERS} onToggle={() => {}} />)
  expect(within(screen.getByTestId('layers-panel')).getAllByRole('listitem')).toHaveLength(3)

  expect(screen.getByText('EMA(20)')).toBeInTheDocument()
  expect(screen.getByTestId('layer-swatch:pline:R1')).toHaveStyle({ backgroundColor: '#dc2626' })

  // visible → checked; hidden → unchecked.
  expect(within(screen.getByTestId('layer-row:overlay:ema:20')).getByRole('checkbox')).toBeChecked()
  expect(
    within(screen.getByTestId('layer-row:marker:bullish')).getByRole('checkbox'),
  ).not.toBeChecked()
})

it('fires onToggle with the row id when a checkbox is clicked', () => {
  const onToggle = jest.fn()
  render(<LayersPanel layers={LAYERS} onToggle={onToggle} />)
  fireEvent.click(within(screen.getByTestId('layer-row:pline:R1')).getByRole('checkbox'))
  expect(onToggle).toHaveBeenCalledWith('pline:R1')
})

// Plan 0065 phase 2: an indicator overlay carries a glossaryKey, so its legend
// label gets an on-hover/on-focus definition; markers/price-lines do not.
const GLOSSARY_LAYERS: ChartLayer[] = [
  {
    id: 'overlay:ema:20',
    label: 'EMA(20)',
    color: '#2563eb',
    kind: 'overlay',
    visible: true,
    glossaryKey: 'ema',
  },
  {
    id: 'marker:bullish',
    label: 'Bullish markers',
    color: '#16a34a',
    kind: 'marker',
    visible: true,
  },
]

it('wraps an indicator-overlay label in a glossary trigger and leaves a marker label plain', () => {
  render(<LayersPanel layers={GLOSSARY_LAYERS} onToggle={() => {}} />)

  const overlayRow = screen.getByTestId('layer-row:overlay:ema:20')
  const trigger = within(overlayRow).getByText('EMA(20)', { selector: '[data-glossary-term]' })
  expect(trigger).toHaveAttribute('data-glossary-term', 'ema')

  const markerRow = screen.getByTestId('layer-row:marker:bullish')
  expect(markerRow.querySelector('[data-glossary-term]')).toBeNull()
  expect(markerRow).toHaveTextContent('Bullish markers')
})

it('surfaces the overlay definition card on focus', () => {
  render(<LayersPanel layers={GLOSSARY_LAYERS} onToggle={() => {}} />)
  const trigger = screen.getByText('EMA(20)', { selector: '[data-glossary-term]' })
  fireEvent.focus(trigger)
  const card = document.getElementById(trigger.getAttribute('aria-describedby') ?? '')
  expect(card).not.toBeNull()
  expect(card).toHaveAttribute('data-visible', 'true')
  expect(card?.textContent).toMatch(/Exponential Moving Average/)
})

it('still toggles an overlay layer via its checkbox (legend interactivity untouched)', () => {
  const onToggle = jest.fn()
  render(<LayersPanel layers={GLOSSARY_LAYERS} onToggle={onToggle} />)
  fireEvent.click(screen.getByLabelText('Toggle EMA(20)'))
  expect(onToggle).toHaveBeenCalledWith('overlay:ema:20')
})

// Plan 0071 phase 2: the same grouped-legend rows Plan 0067 introduced for
// trendlines now serve candlestick-marker groups — a row carries an instance
// count and a highlight key, and hovering it drives onHighlight (enter → key,
// leave → null). This is the generalisation the plan reuses, unchanged.
const GROUPED_LAYERS: ChartLayer[] = [
  {
    id: 'candles:hammer|bullish_marker',
    label: 'Hammer (bullish)',
    color: '#16a34a',
    kind: 'marker',
    visible: true,
    count: 4,
    highlightKey: 'hammer|bullish_marker',
  },
]

it('renders a grouped row with its instance count', () => {
  render(<LayersPanel layers={GROUPED_LAYERS} onToggle={() => {}} />)
  expect(screen.getByTestId('layer-count:candles:hammer|bullish_marker')).toHaveTextContent('4')
})

it('fires onHighlight with the row key on hover-enter and null on leave', () => {
  const onHighlight = jest.fn()
  render(<LayersPanel layers={GROUPED_LAYERS} onToggle={() => {}} onHighlight={onHighlight} />)
  const row = screen.getByTestId('layer-row:candles:hammer|bullish_marker')
  fireEvent.mouseEnter(row)
  expect(onHighlight).toHaveBeenLastCalledWith('hammer|bullish_marker')
  fireEvent.mouseLeave(row)
  expect(onHighlight).toHaveBeenLastCalledWith(null)
})

// Plan 0071 follow-up: the panel width is draggable (left-edge handle) and
// persisted; keyboard is the accessible, deterministic path we assert here.
it('renders a resize handle as an accessible vertical separator', () => {
  render(<LayersPanel layers={LAYERS} onToggle={() => {}} />)
  const handle = screen.getByTestId('layers-resize-handle')
  expect(handle).toHaveAttribute('role', 'separator')
  expect(handle).toHaveAttribute('aria-orientation', 'vertical')
  expect(handle).toHaveAttribute('tabindex', '0')
})

it('widens on ArrowLeft, narrows on ArrowRight, and persists the width', () => {
  render(<LayersPanel layers={LAYERS} onToggle={() => {}} />)
  const handle = screen.getByTestId('layers-resize-handle')
  const width = (): number => Number(handle.getAttribute('aria-valuenow'))
  const start = width()

  fireEvent.keyDown(handle, { key: 'ArrowLeft' })
  expect(width()).toBe(start + 16) // widened by one step
  expect(Number(localStorage.getItem('ma.layersPanelWidth'))).toBe(start + 16)

  fireEvent.keyDown(handle, { key: 'ArrowRight' })
  expect(width()).toBe(start) // back to where it began
  // The panel's inline width tracks the handle's reported value.
  expect(screen.getByTestId('layers-panel')).toHaveStyle({ width: `${start}px` })
})

it('restores a persisted width on mount, clamped to the allowed range', () => {
  localStorage.setItem('ma.layersPanelWidth', '9999') // above MAX_PANEL_WIDTH
  render(<LayersPanel layers={LAYERS} onToggle={() => {}} />)
  // Clamped to the 600px ceiling, not the raw stored value.
  expect(screen.getByTestId('layers-panel')).toHaveStyle({ width: '600px' })
})

// Plan 0082 phase 4 (ADR-0077): the add-indicator form + provenance-scoped
// remove control. The form is only present when `onAddOverlay` is given.

it('renders (not null) with no layers when an add handler is provided, so the first add is reachable', () => {
  render(<LayersPanel layers={[]} onToggle={() => {}} onAddOverlay={() => {}} />)
  expect(screen.getByTestId('layers-panel')).toBeInTheDocument()
  expect(screen.getByTestId('add-overlay-toggle')).toBeInTheDocument()
})

it('has no add affordance when onAddOverlay is absent', () => {
  render(<LayersPanel layers={LAYERS} onToggle={() => {}} />)
  expect(screen.queryByTestId('add-overlay-toggle')).toBeNull()
})

it('adds an EMA with its period when the form is submitted', () => {
  const onAddOverlay = jest.fn()
  render(<LayersPanel layers={[]} onToggle={() => {}} onAddOverlay={onAddOverlay} />)
  fireEvent.click(screen.getByTestId('add-overlay-toggle'))
  // Default kind is EMA, default period 20.
  fireEvent.click(screen.getByTestId('add-overlay-submit'))
  expect(onAddOverlay).toHaveBeenCalledWith({ kind: 'ema', period: 20 })
})

it('shows a std-dev input for Bollinger and adds {bbands, period, multiplier}', () => {
  const onAddOverlay = jest.fn()
  render(<LayersPanel layers={[]} onToggle={() => {}} onAddOverlay={onAddOverlay} />)
  fireEvent.click(screen.getByTestId('add-overlay-toggle'))
  // EMA has no std-dev input; switching to Bollinger reveals it.
  expect(screen.queryByTestId('add-overlay-stddev')).toBeNull()
  fireEvent.change(screen.getByTestId('add-overlay-kind'), { target: { value: 'bbands' } })
  fireEvent.change(screen.getByTestId('add-overlay-period'), { target: { value: '20' } })
  fireEvent.change(screen.getByTestId('add-overlay-stddev'), { target: { value: '2.5' } })
  fireEvent.click(screen.getByTestId('add-overlay-submit'))
  expect(onAddOverlay).toHaveBeenCalledWith({ kind: 'bbands', period: 20, multiplier: 2.5 })
})

it('rejects an invalid period with an accessible message and does not add', () => {
  const onAddOverlay = jest.fn()
  render(<LayersPanel layers={[]} onToggle={() => {}} onAddOverlay={onAddOverlay} />)
  fireEvent.click(screen.getByTestId('add-overlay-toggle'))
  fireEvent.change(screen.getByTestId('add-overlay-period'), { target: { value: '0' } })
  fireEvent.click(screen.getByTestId('add-overlay-submit'))
  const error = screen.getByTestId('add-overlay-error')
  expect(error).toHaveAttribute('role', 'alert')
  expect(error).toHaveTextContent(/greater than 0/i)
  expect(onAddOverlay).not.toHaveBeenCalled()
})

const PROVENANCE_LAYERS: ChartLayer[] = [
  {
    id: 'overlay:ema:20',
    label: 'EMA(20)',
    color: '#2563eb',
    kind: 'overlay',
    visible: true,
    removable: true, // user-added
  },
  {
    id: 'overlay:sma:50',
    label: 'SMA(50)',
    color: '#f97316',
    kind: 'overlay',
    visible: true, // agent — hide-only, no remove
  },
]

it('shows a remove control only on user (removable) rows, and fires onRemove with the row id', () => {
  const onRemove = jest.fn()
  render(<LayersPanel layers={PROVENANCE_LAYERS} onToggle={() => {}} onRemove={onRemove} />)
  // Agent overlay: hide-only (a checkbox, no remove button).
  expect(
    within(screen.getByTestId('layer-row:overlay:sma:50')).getByRole('checkbox'),
  ).toBeInTheDocument()
  expect(screen.queryByTestId('layer-remove:overlay:sma:50')).toBeNull()
  // User overlay: removable.
  fireEvent.click(screen.getByTestId('layer-remove:overlay:ema:20'))
  expect(onRemove).toHaveBeenCalledWith('overlay:ema:20')
})
