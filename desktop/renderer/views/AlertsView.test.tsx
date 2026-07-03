/**
 * Plan 0060 phase 4 done-when:
 * (a) a pushed fake SSE alert prepends a row to the history list (the toast
 *     half lives in AlertToaster.test.tsx — same bus, same push);
 * (b) disabling a watch round-trips through `POST /watches/{id}` and the
 *     list reflects it.
 * Plus: newest-first fetched history, live-vs-fetched dedup on
 * `(watch_id, fired_at)`, empty/error states, and nav reachability.
 */
import '@testing-library/jest-dom'

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

jest.mock('../api/client', () => ({
  api: {
    getWatches: jest.fn(),
    setWatchEnabled: jest.fn(),
    getAlerts: jest.fn(),
  },
  ApiError: class ApiError extends Error {},
}))
// App mounts the SSE stream + the chart view on render; neither is relevant to
// the nav assertion, so stub them out to keep the test fast and deterministic.
jest.mock('../hooks/useEventStream', () => ({ useEventStream: () => undefined }))
jest.mock('./OhlcvView', () => ({ OhlcvView: () => <div data-testid="ohlcv-stub" /> }))

import { api } from '../api/client'
import { App } from '../App'
import { notifyAlert } from '../handlers/alertBus'
import type { AlertTriggeredPayloadV1 } from '../types/events'
import { AlertsView } from './AlertsView'

const getWatches = api.getWatches as jest.Mock
const setWatchEnabled = api.setWatchEnabled as jest.Mock
const getAlerts = api.getAlerts as jest.Mock

function watch(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 1,
    symbol: 'BTC-USD',
    timeframe: '1d',
    kind: 'indicator_threshold',
    params: { indicator: 'rsi', operator: '<', level: 30 },
    interval_seconds: 86_400,
    enabled: true,
    last_state: null,
    created_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

function alertOut(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 10,
    watch_id: 1,
    fired_at: '2026-07-02T00:00:00Z',
    payload: {
      watch_id: 1,
      symbol: 'BTC-USD',
      timeframe: '1d',
      kind: 'indicator_threshold',
      fired_at: '2026-07-02T00:00:00Z',
      condition: 'rsi 28.44 < 30',
      values: { rsi: 28.44, level: 30 },
    },
    ...overrides,
  }
}

function livePayload(overrides: Partial<AlertTriggeredPayloadV1> = {}): AlertTriggeredPayloadV1 {
  return {
    watch_id: 1,
    symbol: 'BTC-USD',
    timeframe: '1d',
    kind: 'indicator_threshold',
    fired_at: '2026-07-03T00:00:00Z',
    condition: 'rsi 27.10 < 30',
    values: { rsi: 27.1, level: 30 },
    ...overrides,
  }
}

beforeEach(() => {
  getWatches.mockReset()
  setWatchEnabled.mockReset()
  getAlerts.mockReset()
  getWatches.mockResolvedValue([])
  getAlerts.mockResolvedValue({ alerts: [], total: 0 })
})

it('renders fetched history newest-first with condition facts', async () => {
  getAlerts.mockResolvedValue({
    alerts: [
      alertOut({ id: 11, fired_at: '2026-07-02T12:00:00Z', payload: condition('newer fact') }),
      alertOut({ id: 10, fired_at: '2026-07-01T12:00:00Z', payload: condition('older fact') }),
    ],
    total: 2,
  })
  render(<AlertsView />)
  const list = await screen.findByTestId('alert-list')
  const rows = within(list).getAllByRole('listitem')
  expect(rows).toHaveLength(2)
  expect(rows[0]).toHaveTextContent('newer fact')
  expect(rows[1]).toHaveTextContent('older fact')
})

it('prepends a row when a fake SSE alert is pushed on the bus', async () => {
  getAlerts.mockResolvedValue({
    alerts: [alertOut({ payload: condition('fetched fact') })],
    total: 1,
  })
  render(<AlertsView />)
  await screen.findByTestId('alert-list')

  act(() => {
    notifyAlert(livePayload({ condition: 'live fact', fired_at: '2026-07-03T00:00:00Z' }))
  })

  const rows = within(screen.getByTestId('alert-list')).getAllByRole('listitem')
  expect(rows).toHaveLength(2)
  expect(rows[0]).toHaveTextContent('live fact')
  expect(rows[1]).toHaveTextContent('fetched fact')
})

it('dedupes a live alert already present in the fetched page', async () => {
  const firedAt = '2026-07-02T00:00:00Z'
  getAlerts.mockResolvedValue({
    alerts: [alertOut({ fired_at: firedAt, payload: condition('the same fire', firedAt) })],
    total: 1,
  })
  render(<AlertsView />)
  await screen.findByTestId('alert-list')

  act(() => {
    notifyAlert(livePayload({ fired_at: firedAt, condition: 'the same fire' }))
  })

  expect(within(screen.getByTestId('alert-list')).getAllByRole('listitem')).toHaveLength(1)
})

it('disabling a watch round-trips and the list reflects it', async () => {
  getWatches.mockResolvedValue([watch()])
  setWatchEnabled.mockResolvedValue(watch({ enabled: false }))
  render(<AlertsView />)
  const row = await screen.findByTestId('watch-row-1')
  expect(row).toHaveTextContent('enabled')

  fireEvent.click(within(row).getByRole('checkbox'))

  await waitFor(() => {
    expect(setWatchEnabled).toHaveBeenCalledWith(1, false)
    expect(screen.getByTestId('watch-row-1')).toHaveTextContent('disabled')
  })
  expect(within(screen.getByTestId('watch-row-1')).getByRole('checkbox')).not.toBeChecked()
})

it('surfaces a toggle failure without dropping the list', async () => {
  getWatches.mockResolvedValue([watch()])
  setWatchEnabled.mockRejectedValue(new Error('sidecar 404: unknown watch_id 1'))
  render(<AlertsView />)
  const row = await screen.findByTestId('watch-row-1')

  fireEvent.click(within(row).getByRole('checkbox'))

  const error = await screen.findByTestId('watch-toggle-error')
  expect(error).toHaveTextContent('unknown watch_id')
  expect(screen.getByTestId('watch-row-1')).toHaveTextContent('enabled')
})

it('renders empty and error states honestly', async () => {
  getWatches.mockResolvedValue([])
  getAlerts.mockRejectedValue(new Error('sidecar 502: upstream down'))
  render(<AlertsView />)
  expect(await screen.findByTestId('watches-empty')).toHaveTextContent('No watches yet')
  expect(await screen.findByTestId('alerts-error')).toHaveTextContent('upstream down')
})

it('is reachable from the nav', async () => {
  render(<App />)
  fireEvent.click(screen.getByTestId('nav-alerts'))
  expect(await screen.findByRole('region', { name: 'Alerts' })).toBeInTheDocument()
})

/** Build an AlertOut payload dict with a given condition string. */
function condition(text: string, firedAt = '2026-07-02T00:00:00Z'): Record<string, unknown> {
  return {
    watch_id: 1,
    symbol: 'BTC-USD',
    timeframe: '1d',
    kind: 'indicator_threshold',
    fired_at: firedAt,
    condition: text,
    values: {},
  }
}
