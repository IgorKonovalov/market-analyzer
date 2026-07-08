/**
 * Plan 0037 phase 2 done-when: the ForecastView panel.
 *
 * Defends: a `forecast.completed v1` envelope driven through the real
 * dispatcher renders one block per horizon with the three probabilities, the
 * skill-vs-baseline pair, and provenance; a `beats_baseline: false` block
 * shows an explicit "no edge over baseline" state and renders NO probability
 * bars — and a mixed event (edge at one horizon, no edge at another) renders
 * both states side by side; a marginal probability (0.52, or any probability
 * under `edge_strength: "marginal"`) is not styled as high-conviction — the
 * marginal and no-edge presentations demonstrably differ from the
 * high-conviction one; the v1 feature-set fallback is stated, not hidden; and
 * the SSE payload is Zod-validated in the dispatcher — a malformed forecast
 * never reaches the handler.
 *
 * Plan 0061 phase 3 adds: a dispatched envelope whose provenance carries
 * `fallback_reason` renders the reason in the feature-set footer (the reason
 * survives the Zod parse); an envelope without the field renders exactly
 * today's footer — no new text, no fallback element.
 *
 * Plan 0063 phase 3 adds: a dispatched envelope with an explanation summary
 * renders the ordered drivers, input freshness, and the artifact path (plain
 * text, never a link) inside the "Why" disclosure; one without renders
 * exactly today's view; and the Why expand/collapse is the ONLY interactive
 * element in the panel (the ADR-0025/0029 no-action posture).
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen, within } from '@testing-library/react'

import { dispatchEnvelope } from '../hooks/useEventStream'
import type {
  ForecastCompletedEnvelope,
  ForecastProvenance,
  ForecastValidation,
  HorizonForecast,
  MultiHorizonForecastResult,
} from '../types/events'
import { ForecastView } from './ForecastView'

function validation(overrides: Partial<ForecastValidation>): ForecastValidation {
  return {
    horizon_bars: 1,
    n_splits: 5,
    n_scored: 120,
    skill: 0.61,
    baseline_skill: 0.4,
    persistence_skill: 0.4,
    majority_skill: 0.36,
    beats_baseline: true,
    folds: [],
    ...overrides,
  }
}

function provenance(overrides: Partial<ForecastProvenance>): ForecastProvenance {
  return {
    model_version: 'a1b2c3d4e5f6a7b8c9d0',
    feature_set_id: 'ohlcv_v2_exog',
    training_cutoff: '2026-07-01T00:00:00+00:00',
    seed: 1729,
    lib_versions: { 'scikit-learn': '1.7.0' },
    series_inputs: [
      { series_id: 'alternative.fear_greed', last_point_ts: 1_751_241_600 },
      { series_id: 'coinmetrics.btc.mvrv', last_point_ts: 1_751_241_600 },
    ],
    ...overrides,
  }
}

/** h=1: a clear edge with a decisive probability — the one presentation
 * allowed to read as conviction. */
const CLEAR_BLOCK: HorizonForecast = {
  horizon_bars: 1,
  prob_up: 0.72,
  prob_down: 0.18,
  prob_flat: 0.1,
  validation: validation({ horizon_bars: 1 }),
  edge_margin: 0.21,
  edge_strength: 'clear',
  provenance: provenance({}),
}

/** h=5: the model beat baseline, but thinly — probabilities ship, styled
 * quietly (the 2026-06-08 incident shape: skill 0.492 vs baseline 0.488). */
const MARGINAL_BLOCK: HorizonForecast = {
  horizon_bars: 5,
  prob_up: 0.52,
  prob_down: 0.3,
  prob_flat: 0.18,
  validation: validation({ horizon_bars: 5, skill: 0.492, baseline_skill: 0.488 }),
  edge_margin: 0.004,
  edge_strength: 'marginal',
  provenance: provenance({}),
}

/** h=21: failed the gate — prob_* keys are ABSENT on the wire (exclude_none),
 * exactly as the bus dumps a no-edge block. The validation basis travels. */
const NO_EDGE_BLOCK: HorizonForecast = {
  horizon_bars: 21,
  validation: validation({
    horizon_bars: 21,
    skill: 0.31,
    baseline_skill: 0.44,
    beats_baseline: false,
  }),
  edge_margin: -0.13,
  edge_strength: 'no_edge',
  provenance: provenance({}),
}

/** The mixed event — edge at 1 bar, marginal at 5, no edge at 21 — in ONE
 * envelope, the ADR-0054 "expressible verdict" the panel must render. */
const MIXED_FORECAST: MultiHorizonForecastResult = {
  symbol: 'BTC-USD',
  timeframe: '1d',
  as_of_bar_ts: '2026-07-05T00:00:00+00:00',
  feature_set_id: 'ohlcv_v2_exog',
  horizons: [CLEAR_BLOCK, MARGINAL_BLOCK, NO_EDGE_BLOCK],
}

function envelope(forecast: MultiHorizonForecastResult): ForecastCompletedEnvelope {
  return {
    type: 'forecast.completed',
    version: 1,
    ts: '2026-07-05T00:00:01+00:00',
    payload: { forecast },
  }
}

/** Drive an envelope through the real dispatch → Zod → handler path (mirrors
 * App's wiring) and return what the handler surfaced. */
function throughDispatch(forecast: MultiHorizonForecastResult): MultiHorizonForecastResult | null {
  let captured: MultiHorizonForecastResult | null = null
  dispatchEnvelope(envelope(forecast), {
    onForecastCompleted: (payload) => {
      captured = payload.forecast
    },
  })
  return captured
}

it('renders one block per horizon with probabilities, skill vs baseline, and provenance from a dispatched envelope', () => {
  const captured = throughDispatch(MIXED_FORECAST)
  expect(captured).not.toBeNull()

  render(<ForecastView forecast={captured} />)

  expect(screen.getByTestId('forecast-title')).toHaveTextContent('BTC-USD')

  // One block per horizon — all three, not just the passing ones.
  expect(screen.getByTestId('forecast-block-1')).toBeInTheDocument()
  expect(screen.getByTestId('forecast-block-5')).toBeInTheDocument()
  expect(screen.getByTestId('forecast-block-21')).toBeInTheDocument()

  // The clear block carries all three probabilities.
  expect(screen.getByTestId('forecast-prob-up-1')).toHaveTextContent('0.72')
  expect(screen.getByTestId('forecast-prob-down-1')).toHaveTextContent('0.18')
  expect(screen.getByTestId('forecast-prob-flat-1')).toHaveTextContent('0.10')

  // The skill-vs-baseline pair, per block.
  expect(screen.getByTestId('forecast-skill-1')).toHaveTextContent('0.610')
  expect(screen.getByTestId('forecast-skill-1')).toHaveTextContent('0.400')
  expect(screen.getByTestId('forecast-skill-21')).toHaveTextContent('0.310')
  expect(screen.getByTestId('forecast-skill-21')).toHaveTextContent('0.440')

  // Provenance renders per block: model version (shortened) + training cutoff.
  expect(screen.getByTestId('forecast-provenance-1')).toHaveTextContent('a1b2c3d4e5f6')
  expect(screen.getByTestId('forecast-provenance-1')).toHaveTextContent(/trained through/)

  // Call-level feature-set provenance, with the exogenous series named.
  const featureSet = screen.getByTestId('forecast-feature-set')
  expect(featureSet).toHaveTextContent('ohlcv_v2_exog')
  expect(featureSet).toHaveTextContent('alternative.fear_greed')
})

it('renders a mixed event with edge and no-edge states side by side: the no-edge block shows the explicit state and NO probability bars', () => {
  render(<ForecastView forecast={MIXED_FORECAST} />)

  // The failed horizon: explicit no-edge state, zero probability bars.
  const noEdge = screen.getByTestId('forecast-no-edge-21')
  expect(noEdge).toBeVisible()
  expect(noEdge).toHaveTextContent(/no edge over baseline/i)
  expect(screen.queryByTestId('forecast-probs-21')).not.toBeInTheDocument()
  expect(screen.queryByTestId('forecast-prob-up-21')).not.toBeInTheDocument()

  // Side by side, the passing horizon still shows its bars — one block's
  // failure neither hides nor is hidden by the other's success.
  expect(screen.getByTestId('forecast-probs-1')).toBeInTheDocument()
  expect(screen.queryByTestId('forecast-no-edge-1')).not.toBeInTheDocument()
})

it('does not style a marginal probability as high-conviction: marginal and no-edge presentations differ from the clear one', () => {
  render(<ForecastView forecast={MIXED_FORECAST} />)

  // Clear edge + decisive 0.72: the only emphatic presentation.
  expect(screen.getByTestId('forecast-prob-up-1')).toHaveAttribute('data-emphasis', 'strong')

  // Any probability under a marginal edge stays quiet — even its argmax.
  expect(screen.getByTestId('forecast-prob-up-5')).toHaveAttribute('data-emphasis', 'quiet')

  // Three visibly distinct block presentations, pinned via the styling hooks:
  // clear vs marginal vs no-edge (which has no probability display at all).
  expect(screen.getByTestId('forecast-edge-1')).toHaveAttribute('data-strength', 'clear')
  expect(screen.getByTestId('forecast-edge-5')).toHaveAttribute('data-strength', 'marginal')
  expect(screen.getByTestId('forecast-edge-21')).toHaveAttribute('data-strength', 'no_edge')
  expect(screen.getByTestId('forecast-edge-5')).toHaveTextContent(/marginal edge/i)
  expect(screen.getByTestId('forecast-edge-21')).toHaveTextContent(/no edge/i)
})

it('renders a near-chance 0.52 quietly even under a clear edge — a 0.52 is never conviction', () => {
  const clearButNearChance: MultiHorizonForecastResult = {
    ...MIXED_FORECAST,
    horizons: [
      {
        ...CLEAR_BLOCK,
        prob_up: 0.52,
        prob_down: 0.3,
        prob_flat: 0.18,
      },
    ],
  }
  render(<ForecastView forecast={clearButNearChance} />)
  expect(screen.getByTestId('forecast-prob-up-1')).toHaveTextContent('0.52')
  expect(screen.getByTestId('forecast-prob-up-1')).toHaveAttribute('data-emphasis', 'quiet')
})

it('states the v1 feature-set fallback out loud when no exogenous series were consumed', () => {
  const v1Fallback: MultiHorizonForecastResult = {
    ...MIXED_FORECAST,
    feature_set_id: 'ohlcv_v1',
    horizons: [
      {
        ...CLEAR_BLOCK,
        provenance: provenance({ feature_set_id: 'ohlcv_v1', series_inputs: [] }),
      },
    ],
  }
  render(<ForecastView forecast={v1Fallback} />)
  const featureSet = screen.getByTestId('forecast-feature-set')
  expect(featureSet).toHaveTextContent('ohlcv_v1')
  expect(featureSet).toHaveTextContent(/no exogenous series were consumed/i)
})

it('renders the fallback reason in the feature-set footer when a dispatched envelope carries one', () => {
  const reason =
    'v2 unavailable: exogenous store has insufficient history ' +
    '(0 of 220 bars survived the join; the requested walk-forward needs at least 10)'
  const starvedFallback: MultiHorizonForecastResult = {
    ...MIXED_FORECAST,
    feature_set_id: 'ohlcv_v1',
    horizons: [
      {
        ...CLEAR_BLOCK,
        provenance: provenance({
          feature_set_id: 'ohlcv_v1',
          series_inputs: [],
          fallback_reason: reason,
        }),
      },
    ],
  }

  // Through the real dispatch → Zod → handler path: the schema accepts the
  // with-reason shape and the field SURVIVES the parse (pre-0061 the
  // non-strict schema stripped it).
  const captured = throughDispatch(starvedFallback)
  expect(captured).not.toBeNull()
  expect(captured?.horizons[0]?.provenance?.fallback_reason).toBe(reason)

  render(<ForecastView forecast={captured} />)
  const fallback = screen.getByTestId('forecast-fallback-reason')
  expect(fallback).toHaveTextContent(/insufficient history/)
  // One plain sentence in the feature-set footer, beside the v1 statement.
  const featureSet = screen.getByTestId('forecast-feature-set')
  expect(featureSet).toHaveTextContent(/no exogenous series were consumed/i)
  expect(featureSet).toHaveTextContent(reason)
})

it('renders exactly today’s footer when no fallback reason travels (no regression)', () => {
  // The schema accepts the without-field shape (both fixtures predate 0061).
  const captured = throughDispatch(MIXED_FORECAST)
  expect(captured).not.toBeNull()

  render(<ForecastView forecast={captured} />)
  expect(screen.queryByTestId('forecast-fallback-reason')).not.toBeInTheDocument()
  expect(screen.getByTestId('forecast-feature-set')).toHaveTextContent(
    'feature set ohlcv_v2_exog — exogenous series: alternative.fear_greed, coinmetrics.btc.mvrv',
  )
})

/** The Plan 0063 explained shape: one clear block whose provenance carries an
 * explanation summary — ordered drivers plus the artifact's relative path. */
const EXPLAINED_FORECAST: MultiHorizonForecastResult = {
  ...MIXED_FORECAST,
  horizons: [
    {
      ...CLEAR_BLOCK,
      provenance: provenance({
        explanation: {
          top_drivers: [
            { feature: 'funding_rate', importance: 0.041 },
            { feature: 'mayer_multiple', importance: 0.032 },
            { feature: 'rsi_14', importance: 0.018 },
          ],
          artifact: 'forecast/20260707T101530000000Z-BTC-USD-1d/explanation.json',
        },
      }),
    },
  ],
}

it('renders the "Why" drivers, input freshness, and artifact path from a dispatched envelope with an explanation summary', () => {
  const captured = throughDispatch(EXPLAINED_FORECAST)
  expect(captured).not.toBeNull()
  // The summary survives the Zod parse whole (the non-strict schema would
  // otherwise silently strip it — the 0061 lesson, re-pinned).
  expect(captured?.horizons[0]?.provenance?.explanation?.top_drivers).toHaveLength(3)

  render(<ForecastView forecast={captured} />)

  expect(screen.getByTestId('forecast-why')).toBeInTheDocument()

  // Drivers render in the wire's order — importance-descending, as ranked.
  const drivers = within(screen.getByTestId('forecast-why-drivers-1')).getAllByRole('listitem')
  expect(drivers.map((item) => item.querySelector('code')?.textContent)).toEqual([
    'funding_rate',
    'mayer_multiple',
    'rsi_14',
  ])

  // Per-series freshness from series_inputs (epoch seconds → UTC timestamp).
  const freshness = screen.getByTestId('forecast-why-freshness')
  expect(freshness).toHaveTextContent('alternative.fear_greed')
  expect(freshness).toHaveTextContent('2025-06-30')

  // The artifact path is a provenance FACT — plain text, never a link.
  const artifact = screen.getByTestId('forecast-why-artifact')
  expect(artifact).toHaveTextContent('forecast/20260707T101530000000Z-BTC-USD-1d/explanation.json')
  expect(artifact.querySelector('a')).toBeNull()
})

it('renders exactly today’s view when no explanation travels (no regression)', () => {
  const captured = throughDispatch(MIXED_FORECAST)
  expect(captured).not.toBeNull()

  render(<ForecastView forecast={captured} />)
  expect(screen.queryByTestId('forecast-why')).not.toBeInTheDocument()
  expect(screen.queryByTestId('forecast-why-artifact')).not.toBeInTheDocument()
})

it('renders drivers without an artifact line when the sidecar had no runs directory', () => {
  const noArtifact: MultiHorizonForecastResult = {
    ...EXPLAINED_FORECAST,
    horizons: [
      {
        ...CLEAR_BLOCK,
        provenance: provenance({
          explanation: {
            top_drivers: [{ feature: 'funding_rate', importance: 0.041 }],
          },
        }),
      },
    ],
  }
  const captured = throughDispatch(noArtifact)
  expect(captured).not.toBeNull()

  render(<ForecastView forecast={captured} />)
  expect(screen.getByTestId('forecast-why-drivers-1')).toHaveTextContent('funding_rate')
  expect(screen.queryByTestId('forecast-why-artifact')).not.toBeInTheDocument()
})

// Plan 0065 phase 2 (ADR-0060) re-scopes this deliberately: the panel gains
// focusable glossary triggers (informational disclosure), but the no-*action*
// guarantee still holds and is still asserted — zero action controls, and every
// [tabindex] addition is a sanctioned glossary trigger, plus the one Why control.
it('adds only glossary disclosure triggers and the Why control — never an ACTION control (ADR-0060)', () => {
  const { container } = render(<ForecastView forecast={EXPLAINED_FORECAST} />)
  // Still zero action controls: nothing to click that could ever act.
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"]'),
  ).toHaveLength(0)
  // The only focusable-by-tabindex additions are informational glossary triggers.
  const focusable = Array.from(container.querySelectorAll('[tabindex]'))
  expect(focusable.length).toBeGreaterThan(0)
  for (const el of focusable) expect(el).toHaveAttribute('data-glossary-term')
  // Exactly one native disclosure control — the Forecast "Why" — and nothing else.
  expect(container.querySelectorAll('summary')).toHaveLength(1)
})

it('wraps forecast terms in glossary triggers and surfaces the dual-hat card on focus', () => {
  const { container } = render(<ForecastView forecast={EXPLAINED_FORECAST} />)
  const keys = Array.from(container.querySelectorAll('[data-glossary-term]')).map((el) =>
    el.getAttribute('data-glossary-term'),
  )
  // The done-when terms: a metric, a verdict, and a feature-driver indicator.
  expect(keys).toEqual(expect.arrayContaining(['skill', 'edge_strength', 'funding_rate']))

  const skill = container.querySelector('[data-glossary-term="skill"]') as HTMLElement
  fireEvent.focus(skill)
  const card = document.getElementById(skill.getAttribute('aria-describedby') ?? '')
  expect(card).not.toBeNull()
  expect(card).toHaveAttribute('role', 'tooltip')
  expect(card).toHaveAttribute('data-visible', 'true')
  // Both hats are present in the card.
  expect(card?.textContent).toMatch(/How it.s computed/)
  expect(card?.textContent).toMatch(/What it means/)
})

it('shows a clear placeholder before any forecast arrives', () => {
  render(<ForecastView forecast={null} />)
  expect(screen.getByTestId('forecast-empty')).toHaveTextContent(/no forecast yet/i)
})

it('Zod-rejects a malformed payload in the dispatcher — the handler never fires', () => {
  const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  try {
    const handler = jest.fn()
    dispatchEnvelope(
      {
        type: 'forecast.completed',
        version: 1,
        ts: '2026-07-05T00:00:01+00:00',
        // prob_up outside [0, 1] and a junk edge_strength — both schema
        // violations; the payload must be dropped before any state.
        payload: {
          forecast: {
            ...MIXED_FORECAST,
            horizons: [{ ...CLEAR_BLOCK, prob_up: 1.7, edge_strength: 'certain' }],
          },
        },
      },
      { onForecastCompleted: handler },
    )
    expect(handler).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('malformed forecast.completed'),
      expect.anything(),
    )
  } finally {
    warn.mockRestore()
  }
})
