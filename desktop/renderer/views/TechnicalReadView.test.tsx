/**
 * Plan 0074 phase 3 done-when: the TechnicalReadView panel (ADR-0068).
 *
 * Defends: a `technical_read.completed v1` envelope driven through the real
 * dispatcher renders direction + indicator + regime_state + rationale; the
 * "single indicator — not corroborated" banner is the panel's prominent first
 * child; there is NO conviction/level field to render and NO interactive control
 * of any kind (the ADR-0068 lesser-tier boundary, enforced as a test); and the
 * SSE payload is Zod-validated in the dispatcher — a malformed read (e.g. an extra
 * conviction field the `.strict()` schema forbids) never reaches the handler.
 */
import '@testing-library/jest-dom'

import { render, screen } from '@testing-library/react'

import { dispatchEnvelope } from '../hooks/useEventStream'
import type { TechnicalRead, TechnicalReadCompletedEnvelope } from '../types/events'
import { TechnicalReadView } from './TechnicalReadView'

const LONG_READ: TechnicalRead = {
  symbol: 'BTC-USD',
  timeframe: '1d',
  as_of_bar_ts: '2026-01-15T00:00:00+00:00',
  indicator_id: 'supertrend',
  direction: 'long',
  regime_state: 'supertrend direction=+1 (uptrend)',
  rationale: ['supertrend rule: long while direction == +1 (active band is the lower band)'],
}

const FLAT_READ: TechnicalRead = {
  symbol: 'SPY',
  timeframe: '1h',
  as_of_bar_ts: '2026-01-10T15:00:00+00:00',
  indicator_id: 'ema_stack',
  direction: 'flat',
  regime_state: 'mixed stack (ema20=418 vs ema50=420, close=419)',
  rationale: ['ema-stack rule: flat when the stack and close do not agree on a side'],
}

function envelope(read: TechnicalRead): TechnicalReadCompletedEnvelope {
  return {
    type: 'technical_read.completed',
    version: 1,
    ts: '2026-01-15T00:00:01+00:00',
    payload: { read },
  }
}

/** Drive an envelope through the real dispatch → Zod → handler path (mirrors
 * App's wiring) and return what the handler surfaced. */
function throughDispatch(read: TechnicalRead): TechnicalRead | null {
  let captured: TechnicalRead | null = null
  dispatchEnvelope(envelope(read), {
    onTechnicalReadCompleted: (payload) => {
      captured = payload.read
    },
  })
  return captured
}

it('renders direction, indicator, and regime_state + rationale from a dispatched envelope', () => {
  const captured = throughDispatch(LONG_READ)
  expect(captured).not.toBeNull()

  render(<TechnicalReadView read={captured} />)

  expect(screen.getByTestId('technical-read-title')).toHaveTextContent('BTC-USD')
  const direction = screen.getByTestId('technical-read-direction')
  expect(direction).toHaveTextContent('long')
  expect(direction).toHaveAttribute('data-direction', 'long')
  const indicator = screen.getByTestId('technical-read-indicator')
  expect(indicator).toHaveTextContent('Supertrend')
  expect(indicator).toHaveAttribute('data-indicator', 'supertrend')
  expect(screen.getByTestId('technical-read-regime')).toHaveTextContent('direction=+1')
  expect(screen.getByTestId('technical-read-rationale')).toHaveTextContent(
    /active band is the lower band/,
  )
})

it('renders the not-corroborated banner as the prominent first child (ADR-0068)', () => {
  render(<TechnicalReadView read={LONG_READ} />)
  const banner = screen.getByTestId('not-corroborated-banner')
  expect(banner).toBeVisible()
  expect(banner).toHaveTextContent(/single indicator/i)
  expect(banner).toHaveTextContent(/not corroborated/i)
  // Prominent = first content in the panel, before any read field.
  const view = screen.getByLabelText('Technical read')
  expect(view.firstElementChild).toBe(banner)
})

it('offers NO interactive control and NO conviction/level field — the ADR-0068 lesser-tier boundary', () => {
  const { container } = render(<TechnicalReadView read={LONG_READ} />)
  // Zero action controls of any kind (and, unlike the fused Recommendation view,
  // not even a glossary trigger — nothing here is focusable).
  expect(
    container.querySelectorAll('button, input, select, textarea, a, [role="button"], summary'),
  ).toHaveLength(0)
  expect(container.querySelectorAll('[tabindex]')).toHaveLength(0)
  // Structurally not a ticket: no conviction, no entry/stop/target field is rendered.
  expect(screen.queryByTestId('technical-read-conviction')).not.toBeInTheDocument()
  expect(screen.queryByTestId('technical-read-entry')).not.toBeInTheDocument()
  expect(screen.queryByTestId('technical-read-stop')).not.toBeInTheDocument()
  expect(screen.queryByTestId('technical-read-targets')).not.toBeInTheDocument()
  // Exactly the three fact rows (indicator, direction, regime) — no level rows.
  expect(container.querySelectorAll('dl > div')).toHaveLength(3)
})

it('renders a flat read as flat, with the mechanical rule stated', () => {
  const captured = throughDispatch(FLAT_READ)
  render(<TechnicalReadView read={captured} />)
  const direction = screen.getByTestId('technical-read-direction')
  expect(direction).toHaveAttribute('data-direction', 'flat')
  expect(direction).toHaveTextContent(/no clear direction/)
  expect(screen.getByTestId('technical-read-indicator')).toHaveTextContent('EMA stack')
})

it('shows a clear placeholder before any read arrives', () => {
  render(<TechnicalReadView read={null} />)
  expect(screen.getByTestId('technical-read-empty')).toHaveTextContent(/no technical read yet/i)
})

it('Zod-rejects a malformed payload in the dispatcher — the handler never fires', () => {
  const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  try {
    const handler = jest.fn()
    dispatchEnvelope(
      {
        type: 'technical_read.completed',
        version: 1,
        ts: '2026-01-15T00:00:01+00:00',
        // An extra `conviction` field is exactly what the lesser tier forbids
        // (the `.strict()` mirror of pydantic extra="forbid") — dropped before state.
        payload: { read: { ...LONG_READ, conviction: 0.7 } },
      },
      { onTechnicalReadCompleted: handler },
    )
    expect(handler).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('malformed technical_read.completed'),
      expect.anything(),
    )
  } finally {
    warn.mockRestore()
  }
})
