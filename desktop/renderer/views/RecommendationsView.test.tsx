/**
 * Plan 0039 phase 2 done-when: the RecommendationsView panel.
 *
 * Defends: a `recommendation.completed v1` envelope driven through the real
 * dispatcher renders direction, conviction, rationale, and all four basis
 * components; the advisory label is rendered prominently; there is NO
 * submit/buy/sell/trade control anywhere in the view (the ADR-0025 boundary,
 * enforced as a test); a low-conviction recommendation is not styled as a
 * strong call; and the SSE payload is Zod-validated in the dispatcher — a
 * malformed recommendation never reaches the handler.
 *
 * Plan 0063 phase 3 adds: a dispatched recommendation with checks renders the
 * fusion-trace table (leg, check, threshold, actual, pass/fail as TEXT); a
 * flat verdict's failed checks are visible without expansion; an empty trace
 * renders exactly today's view; and the table adds no interactive element
 * (the ADR-0025/0029 no-action posture, re-asserted with a trace rendered).
 */
import '@testing-library/jest-dom'

import { render, screen, within } from '@testing-library/react'

import { dispatchEnvelope } from '../hooks/useEventStream'
import type { Recommendation, RecommendationCompletedEnvelope } from '../types/events'
import { RecommendationsView } from './RecommendationsView'

const LONG_REC: Recommendation = {
  symbol: 'BTC-USD',
  timeframe: '1d',
  direction: 'long',
  entry_zone: [63100.5, 63400.0],
  stop: 61800.0,
  targets: [65200.0, 67150.0],
  conviction: 0.72,
  rationale: [
    'forecast probability of up-move is 0.60 and beats the naive baseline',
    'live rsi signal agrees (long)',
    'walk-forward sharpe_mean 0.8 across 5 folds',
  ],
  basis: {
    conditions: ['trend: up', 'momentum: rising'],
    signals: ['rsi long on last closed bar'],
    backtest: { sharpe_mean: 0.8, n_splits: 5 },
    forecast: { prob_up: 0.6, beats_baseline: true },
    // Plan 0063: the fusion trace always rides the wire (rendering it is
    // phase 3). A trace-bearing envelope must survive the dispatcher's Zod
    // parse — including a recorded fact whose None threshold is an absent
    // key, exactly as the bus's exclude_none dump produces.
    checks: [
      { leg: 'signal', check: 'live vote: rsi', actual: 'long', passed: true },
      {
        leg: 'backtest',
        check: 'backtested edge positive (sharpe_mean > 0)',
        threshold: 0,
        actual: 0.8,
        passed: true,
      },
    ],
  },
  label: 'advisory',
  as_of_bar_ts: '2026-01-15T00:00:00+00:00',
}

const LOW_CONVICTION_REC: Recommendation = {
  ...LONG_REC,
  conviction: 0.11,
}

// A flat call carries no levels and (here) no backtest leg — entry_zone/stop
// keys are absent, exactly as the bus's exclude_none dump produces.
const FLAT_REC: Recommendation = {
  symbol: 'SPY',
  timeframe: '1h',
  direction: 'flat',
  targets: [],
  conviction: 0,
  rationale: ['no actionable edge', 'forecast does not beat the naive baseline'],
  basis: {
    conditions: ['trend: sideways'],
    signals: ['macd flat'],
    forecast: { beats_baseline: false },
    // Plan 0063: a flat verdict's trace carries its failed gates — the checks
    // table must show them WITHOUT expansion (the honest-flat legibility
    // criterion). The vote is a recorded fact: threshold key absent.
    checks: [
      {
        leg: 'forecast',
        check: 'probabilities shipped (baseline beaten out-of-sample)',
        threshold: true,
        actual: false,
        passed: false,
      },
      { leg: 'signal', check: 'live vote: macd', actual: 'flat', passed: true },
      {
        leg: 'backtest',
        check: 'backtested edge positive (sharpe_mean above zero)',
        threshold: 0,
        actual: -0.4,
        passed: false,
      },
    ],
  },
  label: 'advisory',
  as_of_bar_ts: '2026-01-10T15:00:00+00:00',
}

function envelope(recommendation: Recommendation): RecommendationCompletedEnvelope {
  return {
    type: 'recommendation.completed',
    version: 1,
    ts: '2026-01-15T00:00:01+00:00',
    payload: { recommendation },
  }
}

/** Drive an envelope through the real dispatch → Zod → handler path (mirrors
 * App's wiring) and return what the handler surfaced. */
function throughDispatch(rec: Recommendation): Recommendation | null {
  let captured: Recommendation | null = null
  dispatchEnvelope(envelope(rec), {
    onRecommendationCompleted: (payload) => {
      captured = payload.recommendation
    },
  })
  return captured
}

it('renders direction, conviction, rationale, and all four basis components from a dispatched envelope', () => {
  const captured = throughDispatch(LONG_REC)
  expect(captured).not.toBeNull()

  render(<RecommendationsView recommendation={captured} />)

  expect(screen.getByTestId('recommendation-title')).toHaveTextContent('BTC-USD')
  expect(screen.getByTestId('recommendation-direction')).toHaveTextContent('long')
  expect(screen.getByTestId('recommendation-direction')).toHaveAttribute('data-direction', 'long')
  expect(screen.getByTestId('recommendation-conviction')).toHaveTextContent('0.72')

  const rationale = screen.getByTestId('recommendation-rationale')
  expect(rationale).toHaveTextContent(/beats the naive baseline/)
  expect(rationale).toHaveTextContent(/live rsi signal agrees/)

  // All four basis components (the phase's done-when).
  expect(screen.getByTestId('basis-conditions')).toHaveTextContent('trend: up')
  expect(screen.getByTestId('basis-signals')).toHaveTextContent(/rsi long/)
  expect(screen.getByTestId('basis-backtest')).toHaveTextContent(/sharpe_mean/)
  expect(screen.getByTestId('basis-forecast')).toHaveTextContent(/prob_up/)

  // Advisory levels render with explicit advisory labeling.
  expect(screen.getByTestId('recommendation-entry')).toHaveTextContent('63,100.50')
  expect(screen.getByTestId('recommendation-stop')).toHaveTextContent('61,800.00')
  expect(screen.getByTestId('recommendation-targets')).toHaveTextContent('65,200.00, 67,150.00')
})

it('renders the advisory label prominently (ADR-0029 acceptance criterion)', () => {
  render(<RecommendationsView recommendation={LONG_REC} />)
  const banner = screen.getByTestId('advisory-label')
  expect(banner).toBeVisible()
  expect(banner).toHaveTextContent(/advisory only/i)
  expect(banner).toHaveTextContent(/not an order ticket/i)
  // Prominent = first content in the panel, before any recommendation field.
  const view = screen.getByLabelText('Advisory recommendation')
  expect(view.firstElementChild).toBe(banner)
})

it('offers NO submit/buy/sell/trade control anywhere — the ADR-0025 boundary as a test', () => {
  const { container } = render(<RecommendationsView recommendation={LONG_REC} />)
  // No interactive control of any kind exists in the view: nothing to click,
  // nothing to type into, nothing that could ever place an order.
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"]'),
  ).toHaveLength(0)
  // And no action language that invites one.
  expect(container.textContent ?? '').not.toMatch(/\b(submit|buy|sell|execute|broker)\b/i)
})

it('does not style a low-conviction recommendation as a strong call', () => {
  render(<RecommendationsView recommendation={LOW_CONVICTION_REC} />)
  const conviction = screen.getByTestId('recommendation-conviction')
  expect(conviction).toHaveAttribute('data-strength', 'low')
  expect(conviction).toHaveTextContent(/low/)

  render(<RecommendationsView recommendation={LONG_REC} />)
  const strong = screen.getAllByTestId('recommendation-conviction')[1]
  expect(strong).toHaveAttribute('data-strength', 'high')
})

it('renders an honest flat: no levels, zero conviction, absent basis leg said out loud', () => {
  const captured = throughDispatch(FLAT_REC)
  expect(captured).not.toBeNull()

  render(<RecommendationsView recommendation={captured} />)

  expect(screen.getByTestId('recommendation-direction')).toHaveTextContent(/no actionable edge/)
  expect(screen.queryByTestId('recommendation-entry')).not.toBeInTheDocument()
  expect(screen.queryByTestId('recommendation-stop')).not.toBeInTheDocument()
  expect(screen.queryByTestId('recommendation-targets')).not.toBeInTheDocument()
  expect(screen.getByTestId('recommendation-conviction')).toHaveAttribute('data-strength', 'low')
  // The missing backtest leg is stated, not hidden.
  expect(screen.getByTestId('basis-backtest')).toHaveTextContent(/not part of this basis/)
  expect(screen.getByTestId('basis-forecast')).toHaveTextContent(/beats_baseline/)
})

it('renders the fusion checks table from a dispatched envelope — leg, check, threshold, actual, pass/fail as text', () => {
  const captured = throughDispatch(LONG_REC)
  expect(captured).not.toBeNull()
  // The trace survives the Zod parse whole (the ADR-0029 pin move, verified
  // through the real dispatcher).
  expect(captured?.basis.checks).toHaveLength(2)

  render(<RecommendationsView recommendation={captured} />)

  const table = screen.getByTestId('recommendation-checks')
  // Header row + one row per check.
  expect(within(table).getAllByRole('row')).toHaveLength(3)
  // The real numbers travel: threshold 0 vs actual 0.80 on the edge gate.
  const edgeRow = within(table)
    .getByText(/backtested edge positive/)
    .closest('tr')
  expect(edgeRow).not.toBeNull()
  expect(edgeRow).toHaveTextContent('backtest')
  expect(edgeRow).toHaveTextContent('0')
  expect(edgeRow).toHaveTextContent('0.80')
  // Pass/fail is a word, not a color: both gates passed on this call.
  expect(within(table).getAllByText('pass')).toHaveLength(2)
  expect(within(table).queryByText('FAIL')).not.toBeInTheDocument()
  // A recorded fact's absent threshold renders as a dash, never "null".
  const voteRow = within(table).getByText('live vote: rsi').closest('tr')
  expect(voteRow).toHaveTextContent('—')
  expect(voteRow).not.toHaveTextContent(/null|undefined/)
})

it('shows a flat verdict’s failed checks without expansion — the honest flat stays as legible as a call', () => {
  const captured = throughDispatch(FLAT_REC)
  expect(captured).not.toBeNull()

  const { container } = render(<RecommendationsView recommendation={captured} />)

  // The table is immediately visible: no details/summary, nothing to expand.
  expect(container.querySelector('details, summary')).toBeNull()
  const table = screen.getByTestId('recommendation-checks')
  expect(table).toBeVisible()

  // Both failed gates read as FAIL by text, with their real numbers beside.
  const failed = within(table).getAllByText('FAIL')
  expect(failed).toHaveLength(2)
  const edgeRow = within(table)
    .getByText(/backtested edge positive/)
    .closest('tr')
  expect(edgeRow).toHaveTextContent('-0.40')
  expect(edgeRow).toHaveAttribute('data-passed', 'false')
  // The passed fact is distinguishable from the failures — by text.
  const voteRow = within(table).getByText('live vote: macd').closest('tr')
  expect(voteRow).toHaveTextContent('pass')
})

it('renders exactly today’s view when the trace is empty (no regression)', () => {
  const noTrace: Recommendation = {
    ...LONG_REC,
    basis: { ...LONG_REC.basis, checks: [] },
  }
  render(<RecommendationsView recommendation={noTrace} />)
  expect(screen.queryByTestId('recommendation-checks')).not.toBeInTheDocument()
})

it('the checks table adds NO interactive element — the ADR-0025 no-action posture holds with a trace rendered', () => {
  const { container } = render(<RecommendationsView recommendation={FLAT_REC} />)
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"], summary'),
  ).toHaveLength(0)
})

it('shows a clear placeholder before any recommendation arrives', () => {
  render(<RecommendationsView recommendation={null} />)
  expect(screen.getByTestId('recommendation-empty')).toHaveTextContent(/no recommendation yet/i)
})

it('Zod-rejects a malformed payload in the dispatcher — the handler never fires', () => {
  const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  try {
    const handler = jest.fn()
    dispatchEnvelope(
      {
        type: 'recommendation.completed',
        version: 1,
        ts: '2026-01-15T00:00:01+00:00',
        // label is not "advisory" and conviction is out of range — both are
        // schema violations; the payload must be dropped before any state.
        payload: {
          recommendation: { ...LONG_REC, label: 'order', conviction: 7 },
        },
      },
      { onRecommendationCompleted: handler },
    )
    expect(handler).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('malformed recommendation.completed'),
      expect.anything(),
    )
  } finally {
    warn.mockRestore()
  }
})
