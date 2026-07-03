/**
 * Plan 0060 phase 4 done-when (a), toast half: a pushed fake SSE alert
 * renders a toast (most-recent-wins), and dismissing clears it. The bus
 * subscription is cleaned up on unmount.
 */
import '@testing-library/jest-dom'

import { act, fireEvent, render, screen } from '@testing-library/react'

import { listenerCountForTests, notifyAlert } from '../handlers/alertBus'
import type { AlertTriggeredPayloadV1 } from '../types/events'
import { AlertToaster } from './AlertToaster'

function payload(overrides: Partial<AlertTriggeredPayloadV1> = {}): AlertTriggeredPayloadV1 {
  return {
    watch_id: 1,
    symbol: 'BTC-USD',
    timeframe: '1d',
    kind: 'indicator_threshold',
    fired_at: '2026-07-02T00:00:00Z',
    condition: 'rsi 28.44 < 30',
    values: { rsi: 28.44, level: 30 },
    ...overrides,
  }
}

it('renders nothing until an alert arrives, then shows the condition fact', () => {
  render(<AlertToaster />)
  expect(screen.queryByTestId('alert-toaster')).not.toBeInTheDocument()

  act(() => {
    notifyAlert(payload())
  })

  expect(screen.getByTestId('alert-toaster')).toBeInTheDocument()
  expect(screen.getByTestId('toast')).toHaveTextContent('Alert: BTC-USD 1d — rsi 28.44 < 30')
})

it('a newer alert replaces the one on screen (most-recent-wins)', () => {
  render(<AlertToaster />)
  act(() => {
    notifyAlert(payload({ condition: 'first fact' }))
    notifyAlert(payload({ condition: 'second fact', fired_at: '2026-07-02T01:00:00Z' }))
  })
  expect(screen.getByTestId('toast')).toHaveTextContent('second fact')
  expect(screen.queryByText(/first fact/)).not.toBeInTheDocument()
})

it('dismiss clears the toast', () => {
  render(<AlertToaster />)
  act(() => {
    notifyAlert(payload())
  })
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }))
  expect(screen.queryByTestId('alert-toaster')).not.toBeInTheDocument()
})

it('unsubscribes from the bus on unmount', () => {
  const before = listenerCountForTests()
  const { unmount } = render(<AlertToaster />)
  expect(listenerCountForTests()).toBe(before + 1)
  unmount()
  expect(listenerCountForTests()).toBe(before)
})
