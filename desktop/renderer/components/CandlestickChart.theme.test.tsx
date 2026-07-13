/**
 * Plan 0033 phase 4 done-when: switching theme recolors the EXISTING chart
 * instance (no remount). The chart reads candle/overlay/marker colors from CSS
 * tokens; on an effective-theme change it re-applies them via `applyOptions`.
 *
 * jsdom doesn't load styles.css, so `getComputedStyle` is stubbed to return a
 * theme-dependent `--chart-up` (keyed off `html[data-theme]`, which `setTheme`
 * sets). The assertion is twofold: createChart is called exactly once across the
 * flip (proving no remount), and the candlestick's `applyOptions` is invoked
 * with the DARK up-color after the flip.
 */
import '@testing-library/jest-dom'

import { act, render } from '@testing-library/react'
import { createChart } from 'lightweight-charts'

import { CandlestickChart } from './CandlestickChart'
import { setTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'

const LIGHT_UP = '#0a645a'
const DARK_UP = '#2dd4bf'

interface FakeCandle {
  setData: jest.Mock
  setMarkers: jest.Mock
  applyOptions: jest.Mock
  attachPrimitive: jest.Mock
  detachPrimitive: jest.Mock
}

let fakeChart: Record<string, unknown>
let candle: FakeCandle

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => fakeChart),
}))

const createChartMock = createChart as unknown as jest.Mock

function buildFakeChart(): Record<string, unknown> {
  candle = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
  }
  const line = (): { setData: jest.Mock; applyOptions: jest.Mock } => ({
    setData: jest.fn(),
    applyOptions: jest.fn(),
  })
  return {
    addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
      candle: () => candle,
      line,
    }),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      getVisibleLogicalRange: jest.fn(() => null),
      setVisibleLogicalRange: jest.fn(),
      subscribeVisibleLogicalRangeChange: jest.fn(),
      unsubscribeVisibleLogicalRangeChange: jest.fn(),
    }),
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  }
}

const realGetComputedStyle = window.getComputedStyle.bind(window)

beforeEach(() => {
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
  createChartMock.mockClear()
  fakeChart = buildFakeChart()
  // Resolve --chart-up from the live data-theme attribute; defer everything else
  // to the real (empty) jsdom declaration so the component falls back as usual.
  jest.spyOn(window, 'getComputedStyle').mockImplementation(((
    el: Element,
    pseudo?: string | null,
  ) => {
    const decl = realGetComputedStyle(el, pseudo ?? undefined)
    const orig = decl.getPropertyValue.bind(decl)
    decl.getPropertyValue = (prop: string): string => {
      if (prop === '--chart-up') {
        return document.documentElement.dataset.theme === 'dark' ? DARK_UP : LIGHT_UP
      }
      return orig(prop)
    }
    return decl
  }) as typeof window.getComputedStyle)
})

afterEach(() => {
  jest.restoreAllMocks()
})

const BARS: Bar[] = Array.from({ length: 10 }, (_, i) => {
  const d = new Date('2026-04-01T00:00:00+00:00')
  d.setUTCDate(d.getUTCDate() + i)
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: d.toISOString(),
    open: 100 + i,
    high: 101 + i,
    low: 99 + i,
    close: 100 + i,
    volume: 1000,
    source: 'test',
  }
})

/** Most recent `upColor` the candlestick was applyOptions'd with. */
function lastUpColor(): unknown {
  const calls = candle.applyOptions.mock.calls
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const arg = calls[i][0] as { upColor?: unknown } | undefined
    if (arg && 'upColor' in arg) return arg.upColor
  }
  return undefined
}

describe('CandlestickChart — theme-aware recolor (Plan 0033 phase 4)', () => {
  it('recolors the existing chart on theme change without remounting', () => {
    render(<CandlestickChart bars={BARS} />)

    // Created once; the candlestick was recolored with the LIGHT up token.
    expect(createChartMock).toHaveBeenCalledTimes(1)
    expect(lastUpColor()).toBe(LIGHT_UP)

    // Flip to dark through the shared theme store (also sets html[data-theme]).
    act(() => {
      setTheme('dark')
    })

    // No remount: createChart was NOT called again. The candlestick was
    // re-applied with the DARK up token.
    expect(createChartMock).toHaveBeenCalledTimes(1)
    expect(lastUpColor()).toBe(DARK_UP)

    // Reset so the explicit choice doesn't leak into other suites.
    act(() => {
      setTheme('system')
    })
  })
})
