/**
 * Plan 0030 phase 2 done-when: the left-edge scroll trigger.
 *
 * A fake chart/time-scale stands in for lightweight-charts (jsdom has no
 * canvas); the test captures the subscribed handler and delivers visible
 * logical ranges by hand. Defends: fires once per inward crossing (not while
 * parked), ignores ranges outside the threshold, respects `enabled`, and
 * unsubscribes on dispose with no late fires.
 */
import '@testing-library/jest-dom'

import { useRef } from 'react'
import { act, render } from '@testing-library/react'
import type { IChartApi, LogicalRange } from 'lightweight-charts'

import { useLazyHistoryTrigger } from './useLazyHistoryTrigger'

let rangeHandler: ((r: LogicalRange | null) => void) | null = null
const unsubscribe = jest.fn()

function buildFakeChart(): IChartApi {
  return {
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: (h: (r: LogicalRange | null) => void) => {
        rangeHandler = h
      },
      unsubscribeVisibleLogicalRangeChange: (h: (r: LogicalRange | null) => void) => {
        unsubscribe(h)
        // The real time scale stops routing events once unsubscribed; mirror
        // that so a post-dispose "delivery" provably reaches no handler.
        if (h === rangeHandler) rangeHandler = null
      },
    }),
  } as unknown as IChartApi
}

let fakeChart: IChartApi

beforeEach(() => {
  rangeHandler = null
  unsubscribe.mockClear()
  fakeChart = buildFakeChart()
})

interface HarnessProps {
  enabled: boolean
  onReach: () => void
  thresholdBars?: number
}

function Harness({ enabled, onReach, thresholdBars }: HarnessProps): JSX.Element {
  const chartRef = useRef<IChartApi | null>(fakeChart)
  useLazyHistoryTrigger(chartRef, { enabled, onReachLeftEdge: onReach, thresholdBars })
  return <div />
}

function deliver(from: number, to: number): void {
  act(() => {
    rangeHandler!({ from, to } as unknown as LogicalRange)
  })
}

it('fires once per inward crossing — not repeatedly while parked at the edge', () => {
  const onReach = jest.fn()
  render(<Harness enabled onReach={onReach} thresholdBars={10} />)

  deliver(5, 50) // inward crossing → fire
  expect(onReach).toHaveBeenCalledTimes(1)

  deliver(4, 49) // still near, parked → no new fire
  deliver(6, 51)
  expect(onReach).toHaveBeenCalledTimes(1)

  deliver(40, 90) // scrolled away from the edge
  deliver(3, 48) // crossed inward again → fire
  expect(onReach).toHaveBeenCalledTimes(2)
})

it('does not fire when the range stays outside the threshold', () => {
  const onReach = jest.fn()
  render(<Harness enabled onReach={onReach} thresholdBars={10} />)

  deliver(50, 120)
  deliver(11, 80)
  expect(onReach).not.toHaveBeenCalled()
})

it('does not fire when disabled, even on an inward crossing', () => {
  const onReach = jest.fn()
  render(<Harness enabled={false} onReach={onReach} thresholdBars={10} />)

  deliver(2, 40)
  expect(onReach).not.toHaveBeenCalled()
})

it('unsubscribes on dispose and never fires afterward', () => {
  const onReach = jest.fn()
  const { unmount } = render(<Harness enabled onReach={onReach} thresholdBars={10} />)

  deliver(5, 50)
  expect(onReach).toHaveBeenCalledTimes(1)

  unmount()
  expect(unsubscribe).toHaveBeenCalledTimes(1)
  // The subscription is gone: the time scale no longer routes events, so no
  // late crossing can reach the callback.
  expect(rangeHandler).toBeNull()
  expect(onReach).toHaveBeenCalledTimes(1)
})
