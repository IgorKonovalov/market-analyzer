/**
 * Plan 0096 phase 2 done-when: the inline chart legend.
 *
 * Defends:
 * - The legend lists the merged layer set (a row per layer) with live values.
 * - The swatch button toggles visibility via the shared onToggle path.
 * - A remove control shows only on user-owned (removable) rows; agent rows are
 *   hide-only (ADR-0077 provenance).
 * - The relocated `+ Indicator` add-form adds a user overlay via onAddOverlay.
 * - The per-row style gear (styleable series only) expands an inline editor.
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen, within } from '@testing-library/react'

import { ChartLegend } from './ChartLegend'
import type { ChartLayer } from './LayersPanel'

const LAYERS: ChartLayer[] = [
  // A user-owned EMA overlay (removable, styleable).
  {
    id: 'overlay:ema:20',
    label: 'EMA 20',
    color: '#ff0000',
    kind: 'overlay',
    visible: true,
    glossaryKey: 'ema',
    removable: true,
  },
  // An agent-pushed SMA overlay (hide-only, styleable).
  {
    id: 'overlay:sma:50',
    label: 'SMA 50',
    color: '#00ff00',
    kind: 'overlay',
    visible: true,
    glossaryKey: 'sma',
  },
  // The always-on OBV strip, currently hidden.
  { id: 'series:obv', label: 'OBV', color: '#0000ff', kind: 'series', visible: false },
  // A trendline group row: no single styleable element ⇒ no gear.
  {
    id: 'trendlines:triangle|solid',
    label: 'Triangle (confirmed)',
    color: '#888888',
    kind: 'trendline',
    visible: true,
    count: 2,
    highlightKey: 'triangle|solid',
  },
]

const VALUES = new Map<string, string>([
  ['overlay:ema:20', '12.34'],
  ['overlay:sma:50', '12.10'],
  ['series:obv', '1000.00'],
])

function renderLegend(over: Partial<React.ComponentProps<typeof ChartLegend>> = {}): {
  onToggle: jest.Mock
  onRemove: jest.Mock
  onAddOverlay: jest.Mock
  onHighlight: jest.Mock
} {
  const onToggle = jest.fn()
  const onRemove = jest.fn()
  const onAddOverlay = jest.fn()
  const onHighlight = jest.fn()
  render(
    <ChartLegend
      layers={LAYERS}
      values={VALUES}
      onToggle={onToggle}
      onRemove={onRemove}
      onAddOverlay={onAddOverlay}
      onHighlight={onHighlight}
      {...over}
    />,
  )
  return { onToggle, onRemove, onAddOverlay, onHighlight }
}

it('lists a row per layer with its live value', () => {
  renderLegend()
  for (const layer of LAYERS) {
    expect(screen.getByTestId(`legend-row:${layer.id}`)).toBeInTheDocument()
  }
  expect(screen.getByTestId('legend-value:overlay:ema:20')).toHaveTextContent('12.34')
  expect(screen.getByTestId('legend-value:series:obv')).toHaveTextContent('1000.00')
  // A count row (trendline group) shows its instance count.
  expect(screen.getByTestId('legend-count:trendlines:triangle|solid')).toHaveTextContent('2')
})

it('the swatch button toggles visibility through the shared onToggle path', () => {
  const { onToggle } = renderLegend()
  fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
  expect(onToggle).toHaveBeenCalledWith('series:obv')
  // The hidden OBV row reads as muted for the eye.
  expect(screen.getByTestId('legend-row:series:obv')).toHaveAttribute('data-hidden', 'true')
})

it('shows a remove control only on user-owned rows and never on agent rows', () => {
  const { onRemove } = renderLegend()
  const remove = screen.getByTestId('legend-remove:overlay:ema:20')
  expect(remove).toBeInTheDocument()
  // The agent-pushed SMA overlay is hide-only — no remove.
  expect(screen.queryByTestId('legend-remove:overlay:sma:50')).not.toBeInTheDocument()
  expect(screen.queryByTestId('legend-remove:series:obv')).not.toBeInTheDocument()
  fireEvent.click(remove)
  expect(onRemove).toHaveBeenCalledWith('overlay:ema:20')
})

it('the relocated add-form adds a user overlay via onAddOverlay', () => {
  const { onAddOverlay } = renderLegend()
  // The add-form is collapsed until the header toggle opens it.
  expect(screen.queryByTestId('add-overlay-form')).not.toBeInTheDocument()
  fireEvent.click(screen.getByTestId('legend-add-toggle'))
  expect(screen.getByTestId('add-overlay-form')).toBeInTheDocument()
  // Submitting the default (EMA / period 20) commits a valid spec.
  fireEvent.click(screen.getByTestId('add-overlay-submit'))
  expect(onAddOverlay).toHaveBeenCalledWith(expect.objectContaining({ kind: 'ema' }))
})

it('omits the add-form entirely when the chart has no (symbol, timeframe)', () => {
  renderLegend({ onAddOverlay: undefined })
  expect(screen.queryByTestId('legend-add-toggle')).not.toBeInTheDocument()
})

it('the style gear expands an inline editor for styleable series only', () => {
  renderLegend()
  // EMA is styleable → gear present; expanding shows the colour editor.
  const gear = screen.getByTestId('legend-settings-toggle:overlay:ema:20')
  expect(screen.queryByTestId('legend-settings:ema')).not.toBeInTheDocument()
  fireEvent.click(gear)
  expect(screen.getByTestId('legend-settings:ema')).toBeInTheDocument()
  expect(
    within(screen.getByTestId('legend-settings:ema')).getByTestId('legend-color:ema'),
  ).toBeInTheDocument()
  // A trendline group has no single styleable element → no gear.
  expect(
    screen.queryByTestId('legend-settings-toggle:trendlines:triangle|solid'),
  ).not.toBeInTheDocument()
})

it('renders nothing when there are no layers and nothing to add', () => {
  const { container } = render(<ChartLegend layers={[]} values={new Map()} onToggle={jest.fn()} />)
  expect(container).toBeEmptyDOMElement()
})
