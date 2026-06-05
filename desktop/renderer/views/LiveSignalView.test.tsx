/**
 * Plan 0026 phase 3 done-when: the LiveSignalView panel.
 *
 * Defends: a `signal.evaluated v1` envelope driven through the real event
 * handler renders the evaluation's position / last-signal / freshness; the
 * empty state shows a clear placeholder; a forming-bar exclusion is surfaced;
 * a fresh signal is visually distinct from a stale one; and the panel carries
 * no buy/sell/recommendation language (it is a condition report).
 */
import '@testing-library/jest-dom'

import { render, screen } from '@testing-library/react'

import { dispatchEnvelope } from '../hooks/useEventStream'
import type { SignalEvaluatedEnvelope, SignalEvaluation } from '../types/events'
import { LiveSignalView } from './LiveSignalView'

const FRESH_LONG: SignalEvaluation = {
  strategy_id: 'rsi',
  symbol: 'AAPL',
  timeframe: '1d',
  evaluated_through_ts: '2026-01-15T00:00:00+00:00',
  closed_bar_count: 15,
  latest_bar_excluded_as_forming: false,
  current_position: 'long',
  last_signal: {
    kind: 'enter_long',
    bar_index: 14,
    event_ts: '2026-01-15T00:00:00+00:00',
    reason: 'RSI crossed below oversold',
  },
  bars_since_last_signal: 0,
  fresh_signal: true,
}

const STALE_FLAT: SignalEvaluation = {
  strategy_id: 'macd',
  symbol: 'SPY',
  timeframe: '1h',
  evaluated_through_ts: '2026-01-10T15:00:00+00:00',
  closed_bar_count: 200,
  latest_bar_excluded_as_forming: false,
  current_position: 'flat',
  // reason omitted (absent on the wire via exclude_none)
  last_signal: { kind: 'exit_long', bar_index: 190, event_ts: '2026-01-10T05:00:00+00:00' },
  bars_since_last_signal: 9,
  fresh_signal: false,
}

const NO_SIGNAL: SignalEvaluation = {
  strategy_id: 'rsi',
  symbol: 'AAPL',
  timeframe: '1d',
  evaluated_through_ts: '2026-01-15T00:00:00+00:00',
  closed_bar_count: 5,
  latest_bar_excluded_as_forming: false,
  current_position: 'flat',
  // last_signal + bars_since_last_signal absent (no signal fired)
  fresh_signal: false,
}

const FORMING: SignalEvaluation = { ...FRESH_LONG, latest_bar_excluded_as_forming: true }

function envelope(evaluation: SignalEvaluation): SignalEvaluatedEnvelope {
  return {
    type: 'signal.evaluated',
    version: 1,
    ts: '2026-01-15T00:00:01+00:00',
    payload: { evaluation },
  }
}

it('renders a fresh long evaluation driven through the event handler', () => {
  // Drive a fixture envelope through the real dispatch → onSignalEvaluated path,
  // then render what the handler surfaced (mirrors App's wiring).
  let captured: SignalEvaluation | null = null
  dispatchEnvelope(envelope(FRESH_LONG), {
    onSignalEvaluated: (payload) => {
      captured = payload.evaluation
    },
  })
  expect(captured).not.toBeNull()

  render(<LiveSignalView evaluation={captured} />)

  expect(screen.getByTestId('live-signal-title')).toHaveTextContent('rsi')
  expect(screen.getByTestId('live-signal-title')).toHaveTextContent('AAPL')
  expect(screen.getByTestId('live-signal-position')).toHaveTextContent('long')
  expect(screen.getByTestId('live-signal-position')).toHaveAttribute('data-position', 'long')
  const last = screen.getByTestId('live-signal-last')
  expect(last).toHaveTextContent(/enter long/)
  expect(last).toHaveTextContent(/last closed bar/)
  expect(last).toHaveTextContent(/RSI crossed below oversold/)
  expect(screen.getByTestId('live-signal-freshness')).toHaveAttribute('data-fresh', 'true')
})

it('shows a clear placeholder before any evaluation arrives', () => {
  render(<LiveSignalView evaluation={null} />)
  expect(screen.getByTestId('live-signal-empty')).toHaveTextContent(/no evaluation yet/i)
  expect(screen.getByTestId('live-signal-empty')).toHaveTextContent(/ask the agent/i)
})

it('renders a stale flat evaluation distinctly from a fresh one', () => {
  render(<LiveSignalView evaluation={STALE_FLAT} />)
  expect(screen.getByTestId('live-signal-position')).toHaveTextContent('flat')
  expect(screen.getByTestId('live-signal-last')).toHaveTextContent(/exit long/)
  expect(screen.getByTestId('live-signal-last')).toHaveTextContent(/9 bars ago/)
  const freshness = screen.getByTestId('live-signal-freshness')
  expect(freshness).toHaveAttribute('data-fresh', 'false')
  expect(freshness).toHaveTextContent(/no fresh signal/i)
})

it('renders "none yet" when no signal has fired', () => {
  render(<LiveSignalView evaluation={NO_SIGNAL} />)
  expect(screen.getByTestId('live-signal-last')).toHaveTextContent(/none yet/i)
  expect(screen.getByTestId('live-signal-freshness')).toHaveAttribute('data-fresh', 'false')
})

it('surfaces a still-forming latest bar (honesty field is shown, not hidden)', () => {
  render(<LiveSignalView evaluation={FORMING} />)
  expect(screen.getByTestId('live-signal-forming')).toHaveTextContent(/still forming/i)
})

it('does NOT surface the forming notice when the latest bar is closed', () => {
  render(<LiveSignalView evaluation={FRESH_LONG} />)
  expect(screen.queryByTestId('live-signal-forming')).not.toBeInTheDocument()
})

it('renders conditions only — no buy/sell/recommendation language', () => {
  const { container } = render(<LiveSignalView evaluation={FRESH_LONG} />)
  const text = container.textContent ?? ''
  expect(text).not.toMatch(/\b(buy|sell|recommend)/i)
})
