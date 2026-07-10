import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'

import { useFormingBar } from './useFormingBar'
import type { MainSeries } from '../lib/chartSeries'
import type { Bar } from '../types/sidecar/bar'
import type { QuoteResponse } from '../types/sidecar/quote-response'

function fakeSeriesRef(): { ref: RefObject<MainSeries>; update: jest.Mock } {
  const update = jest.fn()
  return { ref: { current: { update } as unknown as MainSeries }, update }
}

// One daily bar opening 2026-04-20T00:00Z.
const BARS: Bar[] = [
  {
    event_ts: '2026-04-20T00:00:00+00:00',
    open: 100,
    high: 105,
    low: 98,
    close: 102,
    volume: 1000,
  } as Bar,
]

function quote(as_of: string, price: number): QuoteResponse {
  return { symbol: 'X', price, as_of } as QuoteResponse
}

describe('useFormingBar', () => {
  it('updates the forming candle when the quote falls inside the last bar period', () => {
    const { ref, update } = fakeSeriesRef()
    renderHook(() =>
      useFormingBar(ref, {
        quote: quote('2026-04-20T12:00:00+00:00', 110),
        bars: BARS,
        timeframe: '1d',
        candleType: 'candles',
      }),
    )
    expect(update).toHaveBeenCalledWith({
      time: Math.floor(Date.parse('2026-04-20T00:00:00+00:00') / 1000),
      open: 100,
      high: 110, // extended by the quote
      low: 98,
      close: 110,
    })
  })

  it('updates a single value for a line/area series', () => {
    const { ref, update } = fakeSeriesRef()
    renderHook(() =>
      useFormingBar(ref, {
        quote: quote('2026-04-20T12:00:00+00:00', 110),
        bars: BARS,
        timeframe: '1d',
        candleType: 'line',
      }),
    )
    expect(update).toHaveBeenCalledWith({
      time: Math.floor(Date.parse('2026-04-20T00:00:00+00:00') / 1000),
      value: 110,
    })
  })

  it('never touches the series when the quote has crossed into the next period', () => {
    const { ref, update } = fakeSeriesRef()
    renderHook(() =>
      useFormingBar(ref, {
        // A full day later → outside [start, start+1d).
        quote: quote('2026-04-21T00:00:00+00:00', 110),
        bars: BARS,
        timeframe: '1d',
        candleType: 'candles',
      }),
    )
    expect(update).not.toHaveBeenCalled()
  })

  it('is a no-op with no quote or no bars', () => {
    const a = fakeSeriesRef()
    renderHook(() =>
      useFormingBar(a.ref, { quote: null, bars: BARS, timeframe: '1d', candleType: 'candles' }),
    )
    expect(a.update).not.toHaveBeenCalled()

    const b = fakeSeriesRef()
    renderHook(() =>
      useFormingBar(b.ref, {
        quote: quote('2026-04-20T12:00:00+00:00', 110),
        bars: [],
        timeframe: '1d',
        candleType: 'candles',
      }),
    )
    expect(b.update).not.toHaveBeenCalled()
  })
})
