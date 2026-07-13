/**
 * Plan 0068 phase 2 done-when: the chart consumes the chart-style store for
 * every drawn colour + line width, and a user override re-applies IN PLACE via
 * `applyOptions` with NO chart remount (the creation effect does not re-run).
 * Also: a theme flip still recolours from the (possibly-overridden) tokens, and
 * with no overrides the chart draws exactly today's colours/widths + series set.
 *
 * lightweight-charts is mocked; the fake records each created line series with
 * its construction opts (so volume-MA / VWAP / OBV / overlay can be told apart by
 * `priceScaleId`) and its `applyOptions` calls. `getComputedStyle` is stubbed for
 * `--chart-up` (keyed off `html[data-theme]`) to prove the theme-flip recolor.
 */
import '@testing-library/jest-dom'

import { act, render } from '@testing-library/react'
import { createChart } from 'lightweight-charts'

import { CandlestickChart } from './CandlestickChart'
import { resetChartStyle, setElementOverride } from '../lib/chartStyle'
import { setTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'

const LIGHT_UP = '#0a645a'
const DARK_UP = '#2dd4bf'

interface FakeLine {
  setData: jest.Mock
  applyOptions: jest.Mock
  _opts: { priceScaleId?: string } & Record<string, unknown>
}

let createdLines: FakeLine[] = []
let candle: {
  setData: jest.Mock
  setMarkers: jest.Mock
  applyOptions: jest.Mock
  attachPrimitive: jest.Mock
  detachPrimitive: jest.Mock
  createPriceLine: jest.Mock
  removePriceLine: jest.Mock
}
let fakeChart: Record<string, unknown>

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  TickMarkType: { Year: 0 },
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
    createPriceLine: jest.fn(),
    removePriceLine: jest.fn(),
  }
  return {
    addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
      candle: () => candle,
      line: (opts: unknown) => {
        const s: FakeLine = {
          setData: jest.fn(),
          applyOptions: jest.fn(),
          _opts: (opts ?? {}) as FakeLine['_opts'],
        }
        createdLines.push(s)
        return s
      },
      histogram: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
    }),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      getVisibleRange: jest.fn(() => null),
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
  resetChartStyle()
  delete document.documentElement.dataset.theme
  createChartMock.mockClear()
  createdLines = []
  fakeChart = buildFakeChart()
  jest.spyOn(window, 'getComputedStyle').mockImplementation(((
    el: Element,
    pseudo?: string | null,
  ) => {
    const decl = realGetComputedStyle(el, pseudo ?? undefined)
    const orig = decl.getPropertyValue.bind(decl)
    decl.getPropertyValue = (prop: string): string =>
      prop === '--chart-up'
        ? document.documentElement.dataset.theme === 'dark'
          ? DARK_UP
          : LIGHT_UP
        : orig(prop)
    return decl
  }) as typeof window.getComputedStyle)
})

afterEach(() => {
  act(() => setTheme('system'))
  resetChartStyle()
  jest.restoreAllMocks()
})

const BARS: Bar[] = Array.from({ length: 30 }, (_, i) => {
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

/** The single agent-overlay line series (no explicit priceScaleId). */
function overlayLine(): FakeLine {
  const overlays = createdLines.filter((s) => s._opts.priceScaleId === undefined)
  expect(overlays).toHaveLength(1)
  return overlays[0]
}

/** The line series pinned to a given price scale (volume-MA / VWAP / OBV). */
function lineOnScale(scaleId: string): FakeLine {
  const found = createdLines.find((s) => s._opts.priceScaleId === scaleId)
  expect(found).toBeDefined()
  return found as FakeLine
}

/** Most recent value applyOptions'd for a given key on a series. */
function lastOption(series: FakeLine, key: string): unknown {
  const calls = series.applyOptions.mock.calls
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const arg = calls[i][0] as Record<string, unknown> | undefined
    if (arg && key in arg) return arg[key]
  }
  return undefined
}

describe('CandlestickChart — chart-style overrides (Plan 0068 phase 2)', () => {
  it('with no overrides draws today’s default widths + colours and the same series set', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'ema', period: 20 }]} />)

    // Default widths — the old hard-coded literals.
    expect(lineOnScale('volume')._opts.lineWidth).toBe(1) // volume-MA
    expect(lineOnScale('right')._opts.lineWidth).toBe(2) // VWAP (main scale)
    expect(lineOnScale('obv')._opts.lineWidth).toBe(1) // OBV
    // Overlay defaults: EMA token fallback colour + width 2.
    const ema = overlayLine()
    expect(ema._opts.color).toBe('#2563eb')
    expect(ema._opts.lineWidth).toBe(2)

    // Series set unchanged: candlestick + volume block + one EMA.
    const hook = window.__test_chart_render__
    expect(hook!.seriesKinds).toEqual([
      { kind: 'candlestick' },
      { kind: 'volume' },
      { kind: 'volume_ma' },
      { kind: 'vwap' },
      { kind: 'obv' },
      { kind: 'ema', period: 20 },
    ])
  })

  it('applies an EMA colour + width override in place with no remount', () => {
    render(<CandlestickChart bars={BARS} overlays={[{ kind: 'ema', period: 20 }]} />)
    const ema = overlayLine()
    expect(createChartMock).toHaveBeenCalledTimes(1)

    act(() => {
      // The default (system→light) theme is active; override light's EMA.
      setElementOverride('light', 'ema', { color: '#abcabc', lineWidth: 4 })
    })

    // No remount — the chart instance was NOT recreated.
    expect(createChartMock).toHaveBeenCalledTimes(1)
    // The existing EMA series received BOTH the colour and the width in place.
    expect(lastOption(ema, 'color')).toBe('#abcabc')
    expect(lastOption(ema, 'lineWidth')).toBe(4)
  })

  it('applies a built-in line width override (VWAP) in place', () => {
    render(<CandlestickChart bars={BARS} />)
    const vwap = lineOnScale('right')

    act(() => {
      setElementOverride('light', 'vwap', { lineWidth: 3 })
    })

    expect(createChartMock).toHaveBeenCalledTimes(1)
    expect(lastOption(vwap, 'lineWidth')).toBe(3)
  })

  it('a theme flip still recolours the candlestick, honouring a colour override', () => {
    render(<CandlestickChart bars={BARS} />)
    // The light theme reads the stubbed LIGHT up token at mount.
    expect(lastOption(candle as unknown as FakeLine, 'upColor')).toBe(LIGHT_UP)

    // Override dark's candle-up, then flip to dark: the override wins over the token.
    act(() => {
      setElementOverride('dark', 'candleUp', { color: '#ff00ff' })
      setTheme('dark')
    })
    expect(createChartMock).toHaveBeenCalledTimes(1) // still no remount
    expect(lastOption(candle as unknown as FakeLine, 'upColor')).toBe('#ff00ff')

    // A dark theme with no override for the flipped element falls back to the token.
    expect(lastOption(candle as unknown as FakeLine, 'downColor')).toBeDefined()
  })
})
