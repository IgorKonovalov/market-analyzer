/**
 * Plan 0080 phase 5 done-when (TrackRecordView + App wiring):
 * - renders hit-rate + mean R + sample size + calibration + baseline delta from
 *   a fetched `GET /track_record` aggregate;
 * - a below-`MIN` sample renders the insufficient-sample state and NO conclusive
 *   percentage;
 * - a baseline-mimicking set renders a ~zero delta without spin;
 * - the recent scored calls list shows each call's outcome_class;
 * - zero interactive action elements (a factual record, ADR-0075/ADR-0029);
 * - the view refetches when its `refreshKey` bumps (the `recommendation.scored`
 *   nudge from App);
 * - App does NOT auto-switch to the tab when a `recommendation.scored` lands.
 */
import '@testing-library/jest-dom'

import { act, render, screen, waitFor } from '@testing-library/react'

import type { EventStreamHandlers } from '../hooks/useEventStream'
import type { GetTrackRecordResponse } from '../types/sidecar/get-track-record-response'
import type { ScoredCallOut } from '../types/sidecar/scored-call-out'
import type { TrackRecord } from '../types/sidecar/track-record'

jest.mock('../api/client', () => ({
  api: { getTrackRecord: jest.fn() },
  ApiError: class ApiError extends Error {},
}))
// App mounts the SSE stream + the chart view on render; capture the handlers the
// stream was given (so we can fire `onRecommendationScored`) and stub the chart.
let mockCapturedHandlers: EventStreamHandlers = {}
jest.mock('../hooks/useEventStream', () => ({
  useEventStream: (h: EventStreamHandlers) => {
    mockCapturedHandlers = h
    return { state: 'open' }
  },
}))
jest.mock('./OhlcvView', () => ({ OhlcvView: () => <div data-testid="ohlcv-stub" /> }))

import { api } from '../api/client'
import { App } from '../App'
import { TrackRecordView } from './TrackRecordView'

const getTrackRecord = api.getTrackRecord as jest.Mock

function record(overrides: Partial<TrackRecord> = {}): TrackRecord {
  return {
    n: 24,
    sufficient: true,
    min_n: 20,
    hit_rate: 0.65,
    mean_r: 0.3,
    brier: 0.24,
    calibration_n: 24,
    mean_forecast_prob: 0.6,
    observed_hit_rate: 0.65,
    reliability: [{ lower: 0.6, upper: 0.8, n: 24, mean_predicted: 0.6, observed_freq: 0.65 }],
    baseline_kind: 'buy_and_hold_over_horizon',
    baseline_hit_rate: 0.5,
    hit_rate_vs_baseline: 0.15,
    by_bucket: [],
    ...overrides,
  }
}

function call(overrides: Partial<ScoredCallOut> = {}): ScoredCallOut {
  return {
    symbol: 'DOGE-USD',
    timeframe: '1d',
    strategy_id: 'rsi',
    direction: 'long',
    as_of_bar_ts: '2026-07-01T00:00:00+00:00',
    horizon_bars: 5,
    conviction: 0.6,
    forecast_prob: 0.62,
    outcome_class: 'target_hit',
    realized_return: 0.1,
    realized_r: 1.0,
    directional_correct: true,
    scored_at: '2026-07-06T00:00:00+00:00',
    ...overrides,
  }
}

function response(overrides: Partial<GetTrackRecordResponse> = {}): GetTrackRecordResponse {
  return {
    track_record: record(),
    recent: [call()],
    partial_reason: null,
    message: null,
    total_available: 1,
    offset: 0,
    returned: 1,
    ...overrides,
  }
}

beforeEach(() => {
  getTrackRecord.mockReset()
  mockCapturedHandlers = {}
})

describe('TrackRecordView', () => {
  it('renders hit-rate, mean R, sample size, calibration, and the baseline delta', async () => {
    getTrackRecord.mockResolvedValue(response())
    render(<TrackRecordView />)

    await waitFor(() => expect(screen.getByTestId('track-record-hit-rate')).toBeInTheDocument())
    expect(screen.getByTestId('track-record-hit-rate')).toHaveTextContent('65.0%')
    expect(screen.getByTestId('track-record-mean-r')).toHaveTextContent('+0.30')
    expect(screen.getByTestId('track-record-sample')).toHaveTextContent('24')
    // The baseline delta is the prominent number (an edge over buy-and-hold).
    expect(screen.getByTestId('track-record-baseline-delta')).toHaveTextContent('+15.00%')
    // Calibration read.
    expect(screen.getByTestId('track-record-calibration')).toBeInTheDocument()
    expect(screen.getByTestId('track-record-brier')).toHaveTextContent('0.2400')
  })

  it('renders the insufficient-sample state with no conclusive percentage', async () => {
    getTrackRecord.mockResolvedValue(
      response({
        track_record: record({
          n: 3,
          sufficient: false,
          hit_rate: null,
          mean_r: null,
          brier: null,
          calibration_n: 0,
          mean_forecast_prob: null,
          observed_hit_rate: null,
          reliability: [],
          hit_rate_vs_baseline: null,
        }),
        recent: [call(), call({ as_of_bar_ts: '2026-07-02T00:00:00+00:00' })],
      }),
    )
    render(<TrackRecordView />)

    await waitFor(() => expect(screen.getByTestId('track-record-insufficient')).toBeInTheDocument())
    // No advisor conclusion is rendered on a below-MIN sample.
    expect(screen.queryByTestId('track-record-hit-rate')).not.toBeInTheDocument()
    expect(screen.queryByTestId('track-record-baseline-delta')).not.toBeInTheDocument()
    expect(screen.queryByTestId('track-record-calibration')).not.toBeInTheDocument()
    // ...and no bare percentage leaks into the surface.
    expect(screen.getByTestId('track-record-insufficient').textContent).not.toMatch(/\d%/)
  })

  it('renders a ~zero baseline delta for a set that only matches the baseline', async () => {
    getTrackRecord.mockResolvedValue(
      response({ track_record: record({ hit_rate_vs_baseline: 0.0, baseline_hit_rate: 0.65 }) }),
    )
    render(<TrackRecordView />)

    await waitFor(() =>
      expect(screen.getByTestId('track-record-baseline-delta')).toBeInTheDocument(),
    )
    // Rendered at/near zero, without a spun-up sign.
    expect(screen.getByTestId('track-record-baseline-delta')).toHaveTextContent('0.00%')
  })

  it('lists recent scored calls with their outcome_class', async () => {
    getTrackRecord.mockResolvedValue(
      response({
        recent: [
          call({ symbol: 'AAA', outcome_class: 'target_hit', realized_r: 1.5 }),
          call({ symbol: 'BBB', outcome_class: 'stopped', realized_r: -1.0, direction: 'short' }),
          call({ symbol: 'CCC', outcome_class: 'timeout', realized_r: 0.2 }),
        ],
      }),
    )
    render(<TrackRecordView />)

    await waitFor(() => expect(screen.getByTestId('track-record-recent')).toBeInTheDocument())
    const rows = screen.getAllByTestId('track-record-recent-row')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveAttribute('data-outcome', 'target_hit')
    expect(rows[1]).toHaveAttribute('data-outcome', 'stopped')
    expect(rows[2]).toHaveAttribute('data-outcome', 'timeout')
    expect(rows[1]).toHaveTextContent('Stopped')
  })

  it('has zero interactive action elements (a factual record, never a control surface)', async () => {
    getTrackRecord.mockResolvedValue(response())
    render(<TrackRecordView />)

    await waitFor(() => expect(screen.getByTestId('track-record-hit-rate')).toBeInTheDocument())
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })

  it('refetches when refreshKey bumps (the recommendation.scored nudge)', async () => {
    getTrackRecord.mockResolvedValue(response())
    const { rerender } = render(<TrackRecordView refreshKey={0} />)
    await waitFor(() => expect(getTrackRecord).toHaveBeenCalledTimes(1))
    rerender(<TrackRecordView refreshKey={1} />)
    await waitFor(() => expect(getTrackRecord).toHaveBeenCalledTimes(2))
  })
})

describe('App — track-record tab', () => {
  it('does not auto-switch to the track-record tab on a recommendation.scored event', () => {
    render(<App />)
    // Default view is the chart.
    expect(screen.getByTestId('nav-chart')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('nav-track-record')).not.toHaveAttribute('aria-current', 'page')

    act(() => {
      mockCapturedHandlers.onRecommendationScored?.({
        symbol: 'DOGE-USD',
        timeframe: '1d',
        strategy_id: 'rsi',
        direction: 'long',
        as_of_bar_ts: '2026-07-01T00:00:00+00:00',
        horizon_bars: 5,
        conviction: 0.6,
        forecast_prob: 0.62,
        outcome_class: 'target_hit',
        realized_return: 0.1,
        realized_r: 1.0,
        directional_correct: true,
        scored_at: '2026-07-06T00:00:00+00:00',
      })
    })

    // The scored fact did not grab the screen — still on the chart tab.
    expect(screen.getByTestId('nav-chart')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('nav-track-record')).not.toHaveAttribute('aria-current', 'page')
  })
})
