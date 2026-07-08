/**
 * Plan 0064 phase 5 done-when (hook slice): the trendline recompute trigger.
 *
 * A fake chart/time-scale stands in for lightweight-charts; the test captures
 * the subscribed range handler and delivers range changes by hand, and drives
 * the debounce with fake timers. Defends: mount fires exactly one recompute
 * after the debounce; a burst of range changes coalesces to ONE call; `enabled`
 * gates the fire; dispose clears the pending timer (no late fire) and
 * unsubscribes.
 */
import '@testing-library/jest-dom'

import { useRef } from 'react'
import { act, render } from '@testing-library/react'
import type { IChartApi } from 'lightweight-charts'

import { DEFAULT_RECOMPUTE_DEBOUNCE_MS, useChartPatternRecompute } from './useChartPatternRecompute'

let rangeHandler: (() => void) | null = null
const unsubscribe = jest.fn()

function buildFakeChart(): IChartApi {
  return {
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: (h: () => void) => {
        rangeHandler = h
      },
      unsubscribeVisibleLogicalRangeChange: (h: () => void) => {
        unsubscribe(h)
        if (h === rangeHandler) rangeHandler = null
      },
    }),
  } as unknown as IChartApi
}

let fakeChart: IChartApi

beforeEach(() => {
  jest.useFakeTimers()
  rangeHandler = null
  unsubscribe.mockClear()
  fakeChart = buildFakeChart()
})

afterEach(() => {
  jest.useRealTimers()
})

interface HarnessProps {
  enabled: boolean
  onRecompute: () => void
}

function Harness({ enabled, onRecompute }: HarnessProps): JSX.Element {
  const chartRef = useRef<IChartApi | null>(fakeChart)
  useChartPatternRecompute(chartRef, { enabled, onRecompute })
  return <div />
}

function tick(ms: number): void {
  act(() => {
    jest.advanceTimersByTime(ms)
  })
}

function deliverRangeChange(): void {
  act(() => {
    rangeHandler?.()
  })
}

it('fires exactly one recompute on mount after the debounce settles', () => {
  const onRecompute = jest.fn()
  render(<Harness enabled onRecompute={onRecompute} />)

  // Not yet — the debounce hasn't elapsed.
  tick(DEFAULT_RECOMPUTE_DEBOUNCE_MS - 1)
  expect(onRecompute).not.toHaveBeenCalled()

  tick(1)
  expect(onRecompute).toHaveBeenCalledTimes(1)
})

it('coalesces a burst of range changes into a single recompute', () => {
  const onRecompute = jest.fn()
  render(<Harness enabled onRecompute={onRecompute} />)

  // Rapid pan/zoom events, each within the debounce window → keep resetting.
  deliverRangeChange()
  tick(100)
  deliverRangeChange()
  tick(100)
  deliverRangeChange()
  expect(onRecompute).not.toHaveBeenCalled()

  // Viewport goes quiet → exactly one recompute for the whole burst.
  tick(DEFAULT_RECOMPUTE_DEBOUNCE_MS)
  expect(onRecompute).toHaveBeenCalledTimes(1)
})

it('does not fire when disabled', () => {
  const onRecompute = jest.fn()
  render(<Harness enabled={false} onRecompute={onRecompute} />)

  deliverRangeChange()
  tick(DEFAULT_RECOMPUTE_DEBOUNCE_MS * 2)
  expect(onRecompute).not.toHaveBeenCalled()
})

it('clears the pending timer on dispose and never fires afterward; unsubscribes', () => {
  const onRecompute = jest.fn()
  const { unmount } = render(<Harness enabled onRecompute={onRecompute} />)

  // Unmount before the debounce elapses.
  tick(DEFAULT_RECOMPUTE_DEBOUNCE_MS - 50)
  unmount()
  expect(unsubscribe).toHaveBeenCalledTimes(1)

  tick(DEFAULT_RECOMPUTE_DEBOUNCE_MS * 2)
  expect(onRecompute).not.toHaveBeenCalled()
})
