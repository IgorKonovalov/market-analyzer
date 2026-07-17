/**
 * Plan 0060 phase 4 done-when (a), toast half: a pushed fake SSE alert
 * renders a toast (most-recent-wins), and dismissing clears it. The bus
 * subscription is cleaned up on unmount.
 */
import '@testing-library/jest-dom'

import { act, fireEvent, render, screen } from '@testing-library/react'

import { listenerCountForTests, notifyAlert } from '../handlers/alertBus'
import {
  defiListenerCountForTests,
  notifyDefiPositionAlert,
} from '../handlers/defiPositionAlertBus'
import type { AlertTriggeredPayloadV1, DefiPositionAlertPayloadV1 } from '../types/events'
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

function defiPayload(): DefiPositionAlertPayloadV1 {
  return {
    watch_id: 7,
    wallet: '0x1234…abcd',
    chain: 'base',
    pool_address: `0x${'cd'.repeat(20)}`,
    nft_token_id: 42,
    fired_at: '2026-07-16T09:00:00Z',
    out_since: '2026-07-16T03:00:00Z',
    hours_out: 6.2,
    tick_lower: -100,
    tick_upper: 100,
    current_tick: 150,
    in_range: false,
    uncollected_fees: null,
  }
}

it('a DeFi position alert shows the condition fact as a toast (Plan 0099)', () => {
  render(<AlertToaster />)
  act(() => {
    notifyDefiPositionAlert(defiPayload())
  })
  expect(screen.getByTestId('toast')).toHaveTextContent(
    'LP out of range 6.2h — base pool 0xcdcd…cdcd, tick 150 outside [-100, 100)',
  )
})

it('market and DeFi alerts share the host most-recent-wins', () => {
  render(<AlertToaster />)
  act(() => {
    notifyAlert(payload({ condition: 'rsi fact' }))
    notifyDefiPositionAlert(defiPayload())
  })
  expect(screen.getByTestId('toast')).toHaveTextContent('LP out of range')
  expect(screen.queryByText(/rsi fact/)).not.toBeInTheDocument()
})

it('unsubscribes from the DeFi bus on unmount', () => {
  const before = defiListenerCountForTests()
  const { unmount } = render(<AlertToaster />)
  expect(defiListenerCountForTests()).toBe(before + 1)
  unmount()
  expect(defiListenerCountForTests()).toBe(before)
})
