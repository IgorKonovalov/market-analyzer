import { act, renderHook } from '@testing-library/react'
import type { IChartApi, Time } from 'lightweight-charts'

jest.mock('../api/client', () => ({
  api: { scanPatterns: jest.fn(), scanChartPatterns: jest.fn() },
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      body: string,
    ) {
      super(body)
    }
  },
}))

import { useChartScans } from './useChartScans'
import { ApiError, api } from '../api/client'

const scanPatterns = api.scanPatterns as jest.Mock
const scanChartPatterns = api.scanChartPatterns as jest.Mock

// A chart whose visible range is a numeric [from, to] window.
function chartRef(
  range: { from: Time; to: Time } | null = {
    from: 1609459200 as unknown as Time,
    to: 1625097600 as unknown as Time,
  },
) {
  return {
    current: {
      timeScale: () => ({ getVisibleRange: () => range }),
    } as unknown as IChartApi,
  }
}

beforeEach(() => {
  scanPatterns.mockReset()
  scanChartPatterns.mockReset()
})

describe('useChartScans', () => {
  it('scanVisibleRange reports a count when the sweep publishes markers', async () => {
    scanPatterns.mockResolvedValue({ published: true, count: 3 })
    const { result } = renderHook(() =>
      useChartScans(chartRef(), { symbol: 'BTC-USD', timeframe: '1d' }),
    )
    await act(async () => {
      await result.current.scanVisibleRange()
    })
    expect(scanPatterns).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: 'BTC-USD', timeframe: '1d' }),
    )
    expect(result.current.scanStatus).toEqual({ kind: 'done', count: 3 })
  })

  it('scanVisibleRange reports empty when nothing is published', async () => {
    scanPatterns.mockResolvedValue({ published: false, count: 0 })
    const { result } = renderHook(() =>
      useChartScans(chartRef(), { symbol: 'BTC-USD', timeframe: '1d' }),
    )
    await act(async () => {
      await result.current.scanVisibleRange()
    })
    expect(result.current.scanStatus).toEqual({ kind: 'empty' })
  })

  it('scanVisibleRange surfaces the ApiError message', async () => {
    scanPatterns.mockRejectedValue(new ApiError(500, 'nope'))
    const { result } = renderHook(() =>
      useChartScans(chartRef(), { symbol: 'BTC-USD', timeframe: '1d' }),
    )
    await act(async () => {
      await result.current.scanVisibleRange()
    })
    expect(result.current.scanStatus).toEqual({ kind: 'error', message: 'nope' })
  })

  it('scanChartPatternsVisibleRange tracks its own status independently', async () => {
    scanChartPatterns.mockResolvedValue({ published: true, count: 2 })
    const { result } = renderHook(() =>
      useChartScans(chartRef(), { symbol: 'BTC-USD', timeframe: '1d' }),
    )
    await act(async () => {
      await result.current.scanChartPatternsVisibleRange()
    })
    expect(result.current.chartScanStatus).toEqual({ kind: 'done', count: 2 })
    expect(result.current.scanStatus).toEqual({ kind: 'idle' }) // untouched
  })

  it('does nothing without a symbol/timeframe', async () => {
    const { result } = renderHook(() =>
      useChartScans(chartRef(), { symbol: undefined, timeframe: undefined }),
    )
    await act(async () => {
      await result.current.scanVisibleRange()
    })
    expect(scanPatterns).not.toHaveBeenCalled()
    expect(result.current.scanStatus).toEqual({ kind: 'idle' })
  })
})
