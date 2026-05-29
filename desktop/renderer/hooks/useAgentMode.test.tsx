/**
 * Plan 0014 phase 3 done-when: useAgentMode hook.
 *
 * Defends:
 * - GET /agent_mode fires exactly once on mount; the hook reflects the result.
 * - setEnabled(true) PUTs once; on 200 the local state flips to the server echo.
 * - On a failed PUT the state does NOT flip and the error is exposed.
 * - The hook never PUTs on mount (the toggle persists; mounting must not reset).
 */
import '@testing-library/jest-dom'

import { act, renderHook, waitFor } from '@testing-library/react'

import { useAgentMode } from './useAgentMode'
import { api } from '../api/client'

jest.mock('../api/client', () => ({
  api: {
    getAgentMode: jest.fn(),
    setAgentMode: jest.fn(),
  },
}))

const mockApi = api as unknown as {
  getAgentMode: jest.Mock
  setAgentMode: jest.Mock
}

beforeEach(() => {
  jest.clearAllMocks()
})

it('GETs once on mount and reflects the server state, without PUTting', async () => {
  mockApi.getAgentMode.mockResolvedValue({ enabled: true })

  const { result } = renderHook(() => useAgentMode())

  await waitFor(() => expect(result.current.enabled).toBe(true))
  expect(mockApi.getAgentMode).toHaveBeenCalledTimes(1)
  expect(mockApi.setAgentMode).not.toHaveBeenCalled()
})

it('defaults to disabled before the GET resolves', () => {
  mockApi.getAgentMode.mockReturnValue(new Promise(() => {})) // never resolves

  const { result } = renderHook(() => useAgentMode())

  expect(result.current.enabled).toBe(false)
})

it('setEnabled(true) PUTs once and flips to the server-echoed state on 200', async () => {
  mockApi.getAgentMode.mockResolvedValue({ enabled: false })
  mockApi.setAgentMode.mockResolvedValue({ enabled: true })

  const { result } = renderHook(() => useAgentMode())
  await waitFor(() => expect(mockApi.getAgentMode).toHaveBeenCalledTimes(1))

  act(() => {
    result.current.setEnabled(true)
  })

  await waitFor(() => expect(result.current.enabled).toBe(true))
  expect(mockApi.setAgentMode).toHaveBeenCalledTimes(1)
  expect(mockApi.setAgentMode).toHaveBeenCalledWith(true)
})

it('does NOT flip and exposes the error when the PUT fails', async () => {
  mockApi.getAgentMode.mockResolvedValue({ enabled: false })
  mockApi.setAgentMode.mockRejectedValue(new Error('500: boom'))

  const { result } = renderHook(() => useAgentMode())
  await waitFor(() => expect(mockApi.getAgentMode).toHaveBeenCalledTimes(1))

  act(() => {
    result.current.setEnabled(true)
  })

  await waitFor(() => expect(result.current.error).not.toBeNull())
  expect(result.current.enabled).toBe(false) // unchanged — server rejected it
  expect(result.current.error?.message).toContain('boom')
})
