/**
 * Plan 0047 phase 9: the LayersPanel presentational component in isolation —
 * row rendering, the empty case, swatch colour, and the toggle callback. The
 * chart-integration behaviour (show/hide a drawn layer) lives in
 * CandlestickChart.layers.test.tsx.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { LayersPanel, type ChartLayer } from './LayersPanel'

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
