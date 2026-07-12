/**
 * Plan 0088 phase 5 done-when for `useWalletPnl`. Concrete claims:
 *   1. Starts `idle`; `analyze` transitions idle → loading → ready.
 *   2. `analyze(address, refresh)` calls `api.getWalletPnl` with those args.
 *   3. A rejected fetch transitions to `{status: 'error', error}`.
 *   4. When two analyses race, only the latest response is written (the stale
 *      one is dropped by the monotonic request id).
 */
import { act, renderHook, waitFor } from '@testing-library/react'

import { api } from '../api/client'
import type { WalletPnlResponse } from '../types/defiPnl'
import { useWalletPnl } from './useWalletPnl'

jest.mock('../api/client', () => ({
  api: { getWalletPnl: jest.fn() },
}))

const getWalletPnl = api.getWalletPnl as jest.MockedFunction<typeof api.getWalletPnl>

function fixture(overrides: Partial<WalletPnlResponse> = {}): WalletPnlResponse {
  return {
    wallet: '0x1234…abcd',
    positions: [],
    position_count: 0,
    incomplete: false,
    partial: false,
    incomplete_position_count: 0,
    realized_usd: 0,
    unrealized_usd: 0,
    unclaimed_rewards: null,
    crosscheck_zerion_total: null,
    crosscheck_warning: false,
    ...overrides,
  }
}

const ADDRESS = '0x' + 'a'.repeat(40)

beforeEach(() => {
  getWalletPnl.mockReset()
})

it('starts idle and transitions idle → loading → ready on analyze', async () => {
  getWalletPnl.mockResolvedValue(fixture({ position_count: 2 }))
  const { result } = renderHook(() => useWalletPnl())
  expect(result.current.state).toEqual({ status: 'idle' })

  act(() => result.current.analyze(ADDRESS))
  expect(result.current.state.status).toBe('loading')

  await waitFor(() => expect(result.current.state.status).toBe('ready'))
  if (result.current.state.status !== 'ready') throw new Error('expected ready')
  expect(result.current.state.result.position_count).toBe(2)
})

it('passes the address and refresh flag to the client', async () => {
  getWalletPnl.mockResolvedValue(fixture())
  const { result } = renderHook(() => useWalletPnl())
  act(() => result.current.analyze(ADDRESS, true))
  await waitFor(() =>
    expect(getWalletPnl).toHaveBeenCalledWith({ address: ADDRESS, refresh: true }),
  )
})

it('transitions to error when the fetch rejects', async () => {
  getWalletPnl.mockRejectedValue(new Error('sidecar 503: no wallet-positions source configured'))
  const { result } = renderHook(() => useWalletPnl())
  act(() => result.current.analyze(ADDRESS))
  await waitFor(() => expect(result.current.state.status).toBe('error'))
  if (result.current.state.status !== 'error') throw new Error('expected error')
  expect(result.current.state.error.message).toContain('no wallet-positions source configured')
})

it('drops a stale response when a second analyze supersedes it', async () => {
  let resolveFirst!: (r: WalletPnlResponse) => void
  const first = new Promise<WalletPnlResponse>((res) => {
    resolveFirst = res
  })
  getWalletPnl.mockReturnValueOnce(first)
  getWalletPnl.mockResolvedValueOnce(fixture({ position_count: 99 }))

  const { result } = renderHook(() => useWalletPnl())
  act(() => result.current.analyze(ADDRESS)) // in-flight (first)
  act(() => result.current.analyze(ADDRESS)) // supersedes with the second

  await waitFor(() => expect(result.current.state.status).toBe('ready'))
  if (result.current.state.status !== 'ready') throw new Error('expected ready')
  expect(result.current.state.result.position_count).toBe(99)

  // The stale first response resolving late must NOT clobber the newer result.
  await act(async () => {
    resolveFirst(fixture({ position_count: 1 }))
    await first
  })
  if (result.current.state.status !== 'ready') throw new Error('expected ready')
  expect(result.current.state.result.position_count).toBe(99)
})
