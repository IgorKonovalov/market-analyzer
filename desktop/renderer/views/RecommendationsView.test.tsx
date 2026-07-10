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

import { fireEvent, render, screen, within } from '@testing-library/react'

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
    // Plan 0069 phase 4b: condition/signal codes always ride the wire (rendering
    // them is phase 5). Empty here keeps this pre-phase-5 fixture type-valid.
    condition_codes: [],
    signal_codes: [],
  },
  label: 'advisory',
  as_of_bar_ts: '2026-01-15T00:00:00+00:00',
  // Plan 0069 phase 4: reason_codes always ride the wire (rendering them is
  // phase 5). Empty here keeps this pre-phase-5 fixture type-valid.
  reason_codes: [],
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
    condition_codes: [],
    signal_codes: [],
  },
  label: 'advisory',
  as_of_bar_ts: '2026-01-10T15:00:00+00:00',
  reason_codes: [],
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
  // No ACTION control of any kind exists in the view: nothing to click that
  // could ever place an order. (Plan 0065 / ADR-0060: informational glossary
  // triggers are permitted — asserted below — but they take no action.)
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"]'),
  ).toHaveLength(0)
  // And no action language that invites one.
  expect(container.textContent ?? '').not.toMatch(/\b(submit|buy|sell|execute|broker)\b/i)
  // The only focusable additions are sanctioned glossary disclosure triggers.
  const focusable = Array.from(container.querySelectorAll('[tabindex]'))
  expect(focusable.length).toBeGreaterThan(0)
  for (const el of focusable) expect(el).toHaveAttribute('data-glossary-term')
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

it('the checks table adds only glossary triggers — no ACTION control, no summary (ADR-0060 re-scope)', () => {
  const { container } = render(<RecommendationsView recommendation={FLAT_REC} />)
  // The trace's leg cells become glossary triggers; still zero action controls
  // and no disclosure `summary` (the fusion trace is never behind an expansion).
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"], summary'),
  ).toHaveLength(0)
  const focusable = Array.from(container.querySelectorAll('[tabindex]'))
  expect(focusable.length).toBeGreaterThan(0)
  for (const el of focusable) expect(el).toHaveAttribute('data-glossary-term')
})

it('wraps recommendation terms in glossary triggers and surfaces the dual-hat card on focus', () => {
  const { container } = render(<RecommendationsView recommendation={LONG_REC} />)
  const keys = Array.from(container.querySelectorAll('[data-glossary-term]')).map((el) =>
    el.getAttribute('data-glossary-term'),
  )
  // The done-when terms: conviction, a fusion leg, sharpe_mean, and the levels.
  expect(keys).toEqual(
    expect.arrayContaining([
      'conviction',
      'backtest',
      'sharpe_mean',
      'entry_zone',
      'stop',
      'targets',
    ]),
  )

  const conviction = container.querySelector('[data-glossary-term="conviction"]') as HTMLElement
  fireEvent.focus(conviction)
  const card = document.getElementById(conviction.getAttribute('aria-describedby') ?? '')
  expect(card).not.toBeNull()
  expect(card).toHaveAttribute('data-visible', 'true')
  expect(card?.textContent).toMatch(/How it.s computed/)
  expect(card?.textContent).toMatch(/What it means/)
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

// --------------------------------------------------------------------------- //
// Plan 0066 phase 3: show which forecast tier backed the call.                //
// --------------------------------------------------------------------------- //

const FALLBACK_SENTENCE =
  'v2-full unavailable: 0 of 2746 bars survived the join (floor 500); trained v2-deep (1347 rows)'

// A recommendation whose forecast leg ran v2-deep (a richer tier was skipped),
// carrying the two Plan 0066 tier keys alongside the usual probabilities.
const V2_DEEP_REC: Recommendation = {
  ...LONG_REC,
  basis: {
    ...LONG_REC.basis,
    forecast: {
      prob_up: 0.6,
      beats_baseline: true,
      feature_set_id: '3d8643321ac2cec3',
      fallback_reason: FALLBACK_SENTENCE,
    },
  },
}

it('names the forecast tier and shows the fallback sentence when the basis carries them (through the dispatcher)', () => {
  const captured = throughDispatch(V2_DEEP_REC)
  expect(captured).not.toBeNull()
  // The two scalar keys survive the real Zod parse (open-record basis.forecast).
  expect(captured?.basis.forecast?.feature_set_id).toBe('3d8643321ac2cec3')

  render(<RecommendationsView recommendation={captured} />)

  // The opaque hash is rendered as a readable tier name, not the raw id.
  expect(screen.getByTestId('forecast-tier')).toHaveTextContent(
    'Forecast ran on the v2-deep feature set.',
  )
  expect(screen.getByTestId('forecast-fallback')).toHaveTextContent(FALLBACK_SENTENCE)

  // The tier keys are lifted OUT of the generic fact list: the raw hash and the
  // scalar-key labels never show as fact rows; the other facts still do.
  const forecastBlock = screen.getByTestId('basis-forecast')
  expect(forecastBlock).toHaveTextContent(/prob_up/)
  expect(forecastBlock).not.toHaveTextContent('3d8643321ac2cec3')
  expect(forecastBlock).not.toHaveTextContent('feature_set_id')
})

it('renders exactly today’s view when the forecast tier keys are absent (no regression)', () => {
  // LONG_REC's forecast leg carries no feature_set_id / fallback_reason.
  const captured = throughDispatch(LONG_REC)
  render(<RecommendationsView recommendation={captured} />)

  expect(screen.queryByTestId('forecast-tier')).not.toBeInTheDocument()
  expect(screen.queryByTestId('forecast-fallback')).not.toBeInTheDocument()
  // The scalar facts render exactly as before.
  expect(screen.getByTestId('basis-forecast')).toHaveTextContent(/prob_up/)
})

it('omits the fallback sentence when a genuine v2-full run reports no fallback_reason', () => {
  const v2FullRec: Recommendation = {
    ...LONG_REC,
    basis: {
      ...LONG_REC.basis,
      // A clean richest-tier run: feature_set_id present, no fallback_reason
      // key (exclude_none strips the null on the wire).
      forecast: { prob_up: 0.6, beats_baseline: true, feature_set_id: '2fb15f47d51cbafa' },
    },
  }
  render(<RecommendationsView recommendation={v2FullRec} />)

  expect(screen.getByTestId('forecast-tier')).toHaveTextContent(
    'Forecast ran on the v2-full feature set.',
  )
  expect(screen.queryByTestId('forecast-fallback')).not.toBeInTheDocument()
})

it('degrades gracefully to the raw id for an unmapped feature_set_id', () => {
  const unknownRec: Recommendation = {
    ...LONG_REC,
    basis: {
      ...LONG_REC.basis,
      forecast: { prob_up: 0.6, beats_baseline: true, feature_set_id: 'deadbeefdeadbeef' },
    },
  }
  render(<RecommendationsView recommendation={unknownRec} />)

  // An unknown hash still produces a stated line, just without a friendly name.
  expect(screen.getByTestId('forecast-tier')).toHaveTextContent(
    'Forecast ran on feature set deadbeefdeadbeef.',
  )
})

it('the forecast tier line adds no interactive control (ADR-0025/0029 no-action posture)', () => {
  const { container } = render(<RecommendationsView recommendation={V2_DEEP_REC} />)
  // The added lines are plain text: still zero action controls anywhere, and
  // the tier/fallback paragraphs are not focusable.
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"], summary'),
  ).toHaveLength(0)
  expect(screen.getByTestId('forecast-tier')).not.toHaveAttribute('tabindex')
  expect(screen.getByTestId('forecast-fallback')).not.toHaveAttribute('tabindex')
})

// --------------------------------------------------------------------------- //
// Plan 0069 phase 5: render the authored surfaces from the sidecar reason-codes //
// (localized via t()), not the English prose. The prose fields are set to       //
// placeholder text that must NOT appear — proving the render is code-driven.    //
// --------------------------------------------------------------------------- //

const CODED_REC: Recommendation = {
  ...LONG_REC,
  // Prose that must never render (codes drive the rationale now).
  rationale: ['PROSE_forecast', 'PROSE_signals', 'PROSE_backtest', 'PROSE_conditions'],
  reason_codes: [
    {
      code: 'reason.forecast',
      params: {
        direction: 'long',
        prob: 0.6,
        horizon_bars: 1,
        edge_strength: 'clear',
        skill: 0.61,
        baseline: 0.4,
      },
    },
    { code: 'reason.signals_agree', params: { direction: 'long', strategies: 'rsi' } },
    {
      code: 'reason.backtested_edge',
      params: { sharpe_mean: 0.8, n_splits: 5, strategy_id: 'rsi' },
    },
    { code: 'reason.conditions', params: { trend: 'up', momentum: 'bullish', volume: 'heavy' } },
    // Gate codes, 1:1 with the two basis.checks below (same order).
    { code: 'gate.signal_live_vote', params: { strategy_id: 'rsi' } },
    { code: 'gate.backtest_edge_positive', params: {} },
  ],
  basis: {
    ...LONG_REC.basis,
    conditions: ['PROSE_cond'],
    signals: ['PROSE_sig'],
    // check.check is placeholder prose — the rendered label must come from the
    // gate code, not this string.
    checks: [
      { leg: 'signal', check: 'PROSE_CHECK_vote', actual: 'long', passed: true },
      { leg: 'backtest', check: 'PROSE_CHECK_edge', threshold: 0, actual: 0.8, passed: true },
    ],
    condition_codes: [
      { code: 'condition.trend', params: { value: 'up' } },
      { code: 'condition.candlestick', params: { pattern: 'hammer', direction: 'bullish' } },
    ],
    signal_codes: [
      { code: 'signal.vote', params: { strategy_id: 'rsi', position: 'long', fresh: 1 } },
    ],
  },
}

it('renders the rationale from reason_codes (localized), not the English prose', () => {
  const captured = throughDispatch(CODED_REC)
  expect(captured).not.toBeNull()

  render(<RecommendationsView recommendation={captured} />)
  const rationale = screen.getByTestId('recommendation-rationale')

  expect(rationale).toHaveTextContent(
    'forecast: P(long)=0.6 over 1 bar(s), clear edge (out-of-sample skill 0.61 vs baseline 0.4)',
  )
  expect(rationale).toHaveTextContent('live signals agree (long): rsi')
  expect(rationale).toHaveTextContent(
    'backtested edge: walk-forward sharpe_mean 0.8 over 5 folds (rsi)',
  )
  expect(rationale).toHaveTextContent('conditions: trend=up, momentum=bullish, volume=heavy')
  // The prose fields never render, and no template braces leak.
  expect(rationale).not.toHaveTextContent(/PROSE_/)
  expect(rationale).not.toHaveTextContent(/\{/)
})

it('renders basis.conditions/basis.signals from condition_codes/signal_codes (enum tokens mapped)', () => {
  const captured = throughDispatch(CODED_REC)
  render(<RecommendationsView recommendation={captured} />)

  const conditions = screen.getByTestId('basis-conditions')
  expect(conditions).toHaveTextContent('trend: up')
  expect(conditions).toHaveTextContent('candlestick: hammer (bullish)')
  expect(conditions).not.toHaveTextContent(/PROSE_/)

  const signals = screen.getByTestId('basis-signals')
  expect(signals).toHaveTextContent('rsi: position=long, fresh signal')
  expect(signals).not.toHaveTextContent(/PROSE_/)
})

it('renders the gate-check labels from the per-gate reason-codes, not check.check', () => {
  const captured = throughDispatch(CODED_REC)
  render(<RecommendationsView recommendation={captured} />)

  const table = screen.getByTestId('recommendation-checks')
  // Labels come from the gate codes…
  expect(within(table).getByText('live vote: rsi')).toBeInTheDocument()
  expect(within(table).getByText('backtested edge positive (sharpe_mean > 0)')).toBeInTheDocument()
  // …never the placeholder check.check prose.
  expect(within(table).queryByText(/PROSE_CHECK/)).not.toBeInTheDocument()
  // The dynamic threshold/actual values still travel from the FusionCheck.
  const edgeRow = within(table)
    .getByText('backtested edge positive (sharpe_mean > 0)')
    .closest('tr')
  expect(edgeRow).toHaveTextContent('0.80')
})

it('falls back to the English prose when a payload carries no reason-codes (no regression)', () => {
  // LONG_REC has empty reason_codes/condition_codes/signal_codes → prose renders.
  render(<RecommendationsView recommendation={LONG_REC} />)
  expect(screen.getByTestId('recommendation-rationale')).toHaveTextContent(
    /beats the naive baseline/,
  )
  expect(screen.getByTestId('basis-conditions')).toHaveTextContent('trend: up')
})
