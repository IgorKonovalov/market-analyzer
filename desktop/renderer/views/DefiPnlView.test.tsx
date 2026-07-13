/**
 * Plan 0088 phase 5 done-when: the DeFi Wallet P&L view.
 *
 * Claims:
 *   1. A valid address fetches (refresh=false) and renders the LP-first table
 *      with 7d/30d/90d/all realized figures + a muted estimated-total-return
 *      sub-row (em dash where a window's estimate is null).
 *   2. An invalid address is rejected client-side BEFORE any fetch.
 *   3. Recent addresses persist and re-analyze on click (refresh=false).
 *   4. Non-LP positions render muted in the "Other" section with their reason.
 *   5. The partial banner + excluded count appear only when partial=true.
 *   6. A `no wallet-positions source configured` error renders the actionable
 *      Settings hint, not a raw 500.
 *   7. The DeFi nav tab mounts the view.
 *   8. The summary carries labeled realized/unrealized stats and a copyable id.
 *   9. Each held chain links the wallet to its explorer (OS browser, not in-app);
 *      a `pool_address` deep-links the pool contract.
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// Keep the real ApiError + sanitizeApiErrorBody (the view maps error bodies with
// them); stub only the network method.
jest.mock('../api/client', () => {
  const actual = jest.requireActual('../api/client')
  return { __esModule: true, ...actual, api: { getWalletPnl: jest.fn() } }
})
// App mounts the SSE stream + chart on render; neither is relevant to the nav
// assertion, so stub them out to keep the test fast and deterministic.
jest.mock('../hooks/useEventStream', () => ({ useEventStream: () => undefined }))
jest.mock('./OhlcvView', () => ({ OhlcvView: () => <div data-testid="ohlcv-stub" /> }))

import { ApiError, api } from '../api/client'
import { App } from '../App'
import type { PositionPnl, WalletPnlResponse } from '../types/defiPnl'
import { DefiPnlView } from './DefiPnlView'

const getWalletPnl = api.getWalletPnl as jest.MockedFunction<typeof api.getWalletPnl>

const ADDRESS = '0x' + 'a'.repeat(40)

const LP: PositionPnl = {
  position_id: 'base:aerodrome:weth-usdc',
  is_lp: true,
  realized_usd: 70,
  unrealized_usd: 100,
  cost_basis_usd: 700,
  vs_hodl_usd: 50,
  incomplete: false,
  notes: [],
  windows: [
    { window: '7d', realized_usd: 0, total_return_usd: 30, estimated: true },
    { window: '30d', realized_usd: 10, total_return_usd: null, estimated: true }, // unpriceable → —
    { window: '90d', realized_usd: 40, total_return_usd: 70, estimated: true },
    { window: 'all', realized_usd: 70, total_return_usd: 170, estimated: true },
  ],
  unclaimed_rewards: [{ symbol: 'AERO', amount: 34.2, usd_value: 18 }],
}

const WANDERERS: PositionPnl = {
  position_id: 'base:wanderers:exotic',
  is_lp: false,
  realized_usd: null,
  unrealized_usd: null,
  cost_basis_usd: null,
  vs_hodl_usd: null,
  incomplete: true,
  notes: ['no block-time price for base:0xef0fd52e at ts=1720000000'],
  windows: [],
  unclaimed_rewards: null,
}

function fixture(overrides: Partial<WalletPnlResponse> = {}): WalletPnlResponse {
  return {
    wallet: '0xdead…beef',
    positions: [LP, WANDERERS],
    position_count: 2,
    incomplete: true,
    partial: true,
    incomplete_position_count: 1,
    realized_usd: 70,
    unrealized_usd: 100,
    unclaimed_rewards: [{ symbol: 'AERO', amount: 34.2, usd_value: 18 }],
    crosscheck_zerion_total: null,
    crosscheck_warning: false,
    ...overrides,
  }
}

function analyze(address: string): void {
  fireEvent.change(screen.getByLabelText('Wallet address'), { target: { value: address } })
  fireEvent.click(screen.getByTestId('defi-analyze'))
}

beforeEach(() => {
  window.localStorage.clear()
  getWalletPnl.mockReset()
})

it('fetches a valid address (refresh=false) and renders the LP-first windowed table', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  render(<DefiPnlView />)
  analyze(ADDRESS)

  await waitFor(() =>
    expect(getWalletPnl).toHaveBeenCalledWith({ address: ADDRESS, refresh: false }),
  )

  const lpRow = await screen.findByTestId('defi-lp-row')
  expect(within(lpRow).getByText('base:aerodrome:weth-usdc')).toBeInTheDocument()
  // Exact realized figures across the windows.
  expect(lpRow).toHaveTextContent('$0.00') // 7d realized
  expect(lpRow).toHaveTextContent('+$10.00') // 30d realized
  expect(lpRow).toHaveTextContent('+$70.00') // all realized
  expect(lpRow).toHaveTextContent('$18.00') // unclaimed (summed USD)

  // The muted estimated-total-return sub-row: parenthesized, em dash for the
  // unpriceable 30d window.
  const estRow = screen.getByTestId('defi-est-row')
  expect(estRow).toHaveTextContent('est. return')
  expect(estRow).toHaveTextContent('(+$30.00)') // 7d estimate
  expect(estRow).toHaveTextContent('(+$170.00)') // all estimate
  expect(estRow).toHaveTextContent('—') // 30d estimate is null
})

it('rejects an invalid address client-side without any fetch', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  render(<DefiPnlView />)
  analyze('vitalik.eth')

  expect(await screen.findByTestId('defi-invalid')).toHaveTextContent(/valid 0x/i)
  expect(getWalletPnl).not.toHaveBeenCalled()
})

it('shows the partial banner with the excluded count only when partial=true', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  const { rerender } = render(<DefiPnlView />)
  analyze(ADDRESS)

  const banner = await screen.findByTestId('defi-partial-banner')
  expect(banner).toHaveTextContent('1 of 2 positions excluded')

  // A fully-complete wallet hides the banner.
  getWalletPnl.mockResolvedValue(
    fixture({
      positions: [LP],
      position_count: 1,
      incomplete: false,
      partial: false,
      incomplete_position_count: 0,
    }),
  )
  rerender(<DefiPnlView />)
  analyze(ADDRESS)
  await screen.findByTestId('defi-lp-row')
  expect(screen.queryByTestId('defi-partial-banner')).not.toBeInTheDocument()
})

it('lists a non-LP position muted in the Other section with its reason', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  render(<DefiPnlView />)
  analyze(ADDRESS)

  const otherRow = await screen.findByTestId('defi-other-row')
  expect(within(otherRow).getByText('base:wanderers:exotic')).toBeInTheDocument()
  expect(otherRow).toHaveTextContent(/no block-time price/)
})

it('persists a recent address and re-analyzes it on chip click (refresh=false)', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  render(<DefiPnlView />)
  analyze(ADDRESS)
  await screen.findByTestId('defi-lp-row')

  const chip = await screen.findByTestId('defi-recent-chip')
  expect(chip).toHaveTextContent('0xaaaa…aaaa')
  getWalletPnl.mockClear()

  fireEvent.click(chip)
  await waitFor(() =>
    expect(getWalletPnl).toHaveBeenCalledWith({ address: ADDRESS, refresh: false }),
  )
})

it('maps a missing-source 503 to the actionable Settings hint, not a raw error', async () => {
  getWalletPnl.mockRejectedValue(
    new ApiError(503, JSON.stringify({ detail: 'no wallet-positions source configured' })),
  )
  render(<DefiPnlView />)
  analyze(ADDRESS)

  const alert = await screen.findByTestId('defi-error')
  expect(alert).toHaveTextContent(/set your Zerion API key in Settings/i)
  expect(alert).not.toHaveTextContent('503')
})

it('renders labeled realized/unrealized stats and a copyable full position id', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  render(<DefiPnlView />)
  analyze(ADDRESS)

  const totals = await screen.findByTestId('defi-totals')
  expect(totals).toHaveTextContent('Realized P&L')
  expect(totals).toHaveTextContent('Unrealized P&L')
  expect(totals).toHaveTextContent('+$70.00') // realized total, sign-explicit
  // The display shortens the ref, but the full id stays copyable.
  const copy = await screen.findByTestId('defi-copy-id')
  expect(copy).toHaveAttribute('title', 'Copy full position ID')
})

it('links each held chain to the wallet on its explorer, opened in the OS browser', async () => {
  const openExternal = jest.fn().mockResolvedValue(undefined)
  // @ts-expect-error — minimal window.api stub for this test only.
  window.api = { shell: { openExternal } }
  getWalletPnl.mockResolvedValue(fixture())
  render(<DefiPnlView />)
  analyze(ADDRESS)

  const link = await screen.findByTestId('defi-wallet-link')
  expect(link).toHaveTextContent(/Basescan/)
  fireEvent.click(link)
  expect(openExternal).toHaveBeenCalledWith({
    url: `https://basescan.org/address/${ADDRESS.toLowerCase()}`,
  })
  // @ts-expect-error — tear down the stub.
  delete window.api
})

it('deep-links the pool contract when the sidecar exposes a pool_address', async () => {
  const openExternal = jest.fn().mockResolvedValue(undefined)
  // @ts-expect-error — minimal window.api stub for this test only.
  window.api = { shell: { openExternal } }
  const pool = '0x' + 'b'.repeat(40)
  getWalletPnl.mockResolvedValue(
    fixture({ positions: [{ ...LP, chain: 'base', pool_address: pool }] }),
  )
  render(<DefiPnlView />)
  analyze(ADDRESS)

  const link = await screen.findByTestId('defi-pool-link')
  fireEvent.click(link)
  expect(openExternal).toHaveBeenCalledWith({ url: `https://basescan.org/address/${pool}` })
  // @ts-expect-error — tear down the stub.
  delete window.api
})

it('exposes a DeFi menu item that mounts the Wallet P&L view when selected', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  render(<App />)

  // DeFi folded into the collapsed nav menu (Plan 0096 phase 5) — open it first.
  fireEvent.click(screen.getByTestId('nav-menu-trigger'))
  const defiItem = screen.getByRole('menuitem', { name: 'DeFi' })
  expect(defiItem).toBeInTheDocument()

  fireEvent.click(defiItem)
  expect(await screen.findByRole('region', { name: 'Wallet P&L' })).toBeInTheDocument()
})
