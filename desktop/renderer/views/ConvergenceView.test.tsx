/**
 * Plan 0078 phase 3 done-when: the ConvergenceView panel (ADR-0041/0029).
 *
 * Defends: a `prediction.screen_completed v1` envelope driven through the real
 * dispatcher renders one row per opportunity with the edge + all risk fields; a
 * high-`resolution_risk` row renders its badge + reason and a thin-book row its
 * liquidity caution; an invalid payload is Zod-dropped loudly in the dispatcher
 * before any state; and a clear placeholder shows before any screen arrives.
 *
 * Plan 0089 refines the boundary from "zero interactive elements" to "zero TRADE
 * controls plus exactly one read-only external market link": each card carries a
 * "View on Polymarket ↗" provenance link (host-allowlisted to polymarket.com,
 * opened in the OS browser via shell.openExternal, `rel="noreferrer"`), an
 * off-allowlist URL renders no link, and the view pins the screener's
 * edge-descending order even from an out-of-order payload.
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen } from '@testing-library/react'

import { dispatchEnvelope } from '../hooks/useEventStream'
import type {
  ConvergenceOpportunity,
  PredictionScreenCompletedEnvelope,
  PredictionScreenCompletedPayloadV1,
} from '../types/events'
import { ConvergenceView } from './ConvergenceView'

const HIGH_RISK_THIN: ConvergenceOpportunity = {
  market_id: 'multi',
  question: 'Which candidate wins the disputed election?',
  outcome_label: 'Candidate A',
  implied_probability: 0.93,
  implied_return_if_right: 0.07 / 0.93,
  time_to_resolution: 'P2DT6H',
  capital_lockup_note:
    'Market close is not settlement — UMA resolution can lag hours to days and can be disputed.',
  liquidity_caution: 'Thin book (~$8,000 reported volume) — the implied probability can be stale.',
  resolution_risk: {
    level: 'high',
    reasons: [
      'Question wording contains dispute-prone term(s) (dispute) — resolution may be contested.',
      'Low or unknown volume — thin books resolve less reliably.',
    ],
  },
  volume_usd: 8000,
  closes_at: '2026-07-13T18:00:00Z',
  queried_at: '2026-07-11T12:00:00Z',
  source: 'polymarket',
  market_url: 'https://polymarket.com/event/disputed-election-2026',
}

const LOW_RISK_DEEP: ConvergenceOpportunity = {
  market_id: 'deep',
  question: 'Will it rain in the capital tomorrow?',
  outcome_label: 'No',
  implied_probability: 0.95,
  implied_return_if_right: 0.05 / 0.95,
  time_to_resolution: 'P3D',
  capital_lockup_note: 'Market close is not settlement — capital stays locked until settlement.',
  // Deep book: no caution, and the None-defaulted fields are absent on the wire.
  resolution_risk: {
    level: 'low',
    reasons: ['Binary market with adequate volume — but resolution risk is never zero.'],
  },
  closes_at: '2026-07-14T12:00:00Z',
  queried_at: '2026-07-11T12:00:00Z',
  source: 'polymarket',
  market_url: 'https://polymarket.com/event/will-it-rain-tomorrow',
}

function payload(opportunities: ConvergenceOpportunity[]): PredictionScreenCompletedPayloadV1 {
  return {
    query: 'election',
    opportunities,
    queried_at: '2026-07-11T12:00:00Z',
    source: 'polymarket',
  }
}

function envelope(opportunities: ConvergenceOpportunity[]): PredictionScreenCompletedEnvelope {
  return {
    type: 'prediction.screen_completed',
    version: 1,
    ts: '2026-07-11T12:00:01Z',
    payload: payload(opportunities),
  }
}

/** Drive an envelope through the real dispatch → Zod → handler path (mirrors
 * App's wiring) and return what the handler surfaced. */
function throughDispatch(
  opportunities: ConvergenceOpportunity[],
): PredictionScreenCompletedPayloadV1 | null {
  let captured: PredictionScreenCompletedPayloadV1 | null = null
  dispatchEnvelope(envelope(opportunities), {
    onPredictionScreenCompleted: (p) => {
      captured = p
    },
  })
  return captured
}

it('renders one row per opportunity with the edge + all risk fields', () => {
  const captured = throughDispatch([HIGH_RISK_THIN, LOW_RISK_DEEP])
  expect(captured).not.toBeNull()

  render(<ConvergenceView screen={captured} />)

  const cards = screen.getAllByTestId('convergence-opportunity')
  expect(cards).toHaveLength(2)
  // The query provenance is shown.
  expect(screen.getByTestId('convergence-query')).toHaveTextContent('election')

  // Every card carries the edge + its full risk context.
  for (const card of cards) {
    expect(card.querySelector('[data-testid="opportunity-outcome"]')).toBeTruthy()
    expect(card.querySelector('[data-testid="opportunity-return"]')).toBeTruthy()
    expect(card.querySelector('[data-testid="opportunity-ttr"]')).toBeTruthy()
    expect(card.querySelector('[data-testid="resolution-risk-badge"]')).toBeTruthy()
    expect(card.querySelector('[data-testid="resolution-risk-reasons"]')).toBeTruthy()
    expect(card.querySelector('[data-testid="capital-lockup"]')).toBeTruthy()
  }
  // The gross return is labeled as such (never expected value) and the duration is
  // rendered human-readable, not as a raw ISO duration.
  const firstReturn = cards[0].querySelector('[data-testid="opportunity-return"]')
  expect(firstReturn).toHaveTextContent(/not expected value/i)
  expect(cards[0].querySelector('[data-testid="opportunity-ttr"]')).toHaveTextContent('2d 6h')
})

it('renders an elevated resolution-risk row with its badge + reason', () => {
  render(<ConvergenceView screen={payload([HIGH_RISK_THIN])} />)
  const badge = screen.getByTestId('resolution-risk-badge')
  expect(badge).toHaveAttribute('data-level', 'high')
  expect(badge).toHaveTextContent(/high/i)
  expect(screen.getByTestId('resolution-risk-reasons')).toHaveTextContent(/dispute-prone/i)
})

it('renders a thin-book row with its liquidity caution; a deep-book row shows none', () => {
  render(<ConvergenceView screen={payload([HIGH_RISK_THIN, LOW_RISK_DEEP])} />)
  const [thin, deep] = screen.getAllByTestId('convergence-opportunity')
  const caution = thin.querySelector('[data-testid="liquidity-caution"]')
  expect(caution).toBeTruthy()
  expect(caution).toHaveTextContent(/thin book/i)
  // The deep-book opportunity carries no caution (its field was absent on the wire).
  expect(deep.querySelector('[data-testid="liquidity-caution"]')).toBeNull()
})

it('offers ZERO trade controls, plus exactly one read-only market link per card (Plan 0089)', () => {
  const { container } = render(
    <ConvergenceView screen={payload([HIGH_RISK_THIN, LOW_RISK_DEEP])} />,
  )
  // No trade controls of any kind — the ADR-0029/0041 facts-not-a-call boundary,
  // refined from Plan 0078's "zero interactive elements" to "zero TRADE controls".
  expect(
    container.querySelectorAll('button, input, select, textarea, [role="button"]'),
  ).toHaveLength(0)
  // Exactly one external market link per card — provenance/citation, never a buy
  // control: https, polymarket.com host, rel="noreferrer".
  const links = container.querySelectorAll('a')
  expect(links).toHaveLength(2)
  links.forEach((a) => {
    expect(a.getAttribute('href')).toMatch(/^https:\/\/polymarket\.com\//)
    expect(a).toHaveAttribute('rel', 'noreferrer')
  })
  // No trade-shaped field is rendered anywhere (this is not a Recommendation).
  expect(screen.queryByTestId('opportunity-entry')).not.toBeInTheDocument()
  expect(screen.queryByTestId('opportunity-stop')).not.toBeInTheDocument()
  expect(screen.queryByTestId('opportunity-size')).not.toBeInTheDocument()
})

it('opens the market link in the OS browser on click (never a renderer navigation)', () => {
  const openExternal = jest.fn().mockResolvedValue(undefined)
  // @ts-expect-error — minimal window.api stub for this test only.
  window.api = { shell: { openExternal } }
  try {
    render(<ConvergenceView screen={payload([LOW_RISK_DEEP])} />)
    const link = screen.getByTestId('market-link')
    expect(link).toHaveTextContent(/view on polymarket/i)
    expect(link).toHaveAttribute('href', 'https://polymarket.com/event/will-it-rain-tomorrow')
    fireEvent.click(link)
    expect(openExternal).toHaveBeenCalledWith({
      url: 'https://polymarket.com/event/will-it-rain-tomorrow',
    })
  } finally {
    // @ts-expect-error — tear down the stub.
    delete window.api
  }
})

it('renders no market link when market_url is absent (no dead link)', () => {
  const noUrl = { ...LOW_RISK_DEEP, market_url: undefined }
  render(<ConvergenceView screen={payload([noUrl])} />)
  expect(screen.queryByTestId('market-link')).not.toBeInTheDocument()
})

it.each([
  ['non-polymarket host', 'https://evil.example.com/phish'],
  ['look-alike host', 'https://polymarket.com.evil.com/x'],
  ['http (not https)', 'http://polymarket.com/event/x'],
  ['host with a port', 'https://polymarket.com:8080/event/x'],
  ['garbage', 'not-a-url'],
])('renders no link for an off-allowlist market_url — the renderer allowlist (%s)', (_l, url) => {
  render(<ConvergenceView screen={payload([{ ...LOW_RISK_DEEP, market_url: url }])} />)
  expect(screen.queryByTestId('market-link')).not.toBeInTheDocument()
})

it('renders cards edge-descending even from an out-of-order payload (Plan 0089 sort pin)', () => {
  const low = { ...LOW_RISK_DEEP, market_id: 'low', implied_return_if_right: 0.02 }
  const high = { ...LOW_RISK_DEEP, market_id: 'high', implied_return_if_right: 0.09 }
  const mid = { ...LOW_RISK_DEEP, market_id: 'mid', implied_return_if_right: 0.05 }
  // Deliberately shuffled — the screener guarantees descending, and the view pins it.
  render(<ConvergenceView screen={payload([low, high, mid])} />)
  const ids = screen
    .getAllByTestId('convergence-opportunity')
    .map((el) => el.getAttribute('data-market-id'))
  expect(ids).toEqual(['high', 'mid', 'low'])
})

it('shows a clear placeholder before any screen arrives', () => {
  render(<ConvergenceView screen={null} />)
  expect(screen.getByTestId('convergence-empty')).toHaveTextContent(/no convergence screen yet/i)
})

it('Zod-rejects a malformed payload in the dispatcher — the handler never fires', () => {
  const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  try {
    const handler = jest.fn()
    dispatchEnvelope(
      {
        type: 'prediction.screen_completed',
        version: 1,
        ts: '2026-07-11T12:00:01Z',
        // An extra `direction` field on an opportunity is exactly what the
        // facts-not-a-call boundary forbids (the `.strict()` mirror of the frozen
        // pydantic model) — dropped before any state.
        payload: {
          query: 'x',
          opportunities: [{ ...HIGH_RISK_THIN, direction: 'long' }],
          queried_at: '2026-07-11T12:00:00Z',
          source: 'polymarket',
        },
      },
      { onPredictionScreenCompleted: handler },
    )
    expect(handler).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('malformed prediction.screen_completed'),
      expect.anything(),
    )
  } finally {
    warn.mockRestore()
  }
})
