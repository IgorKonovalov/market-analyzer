/**
 * Plan 0043 phase 2 done-when: the Portfolio view + DeFi risk panel.
 *
 * Claims (asserted at the DOM level):
 *   1. Holdings/P&L/exposure render, and each venue leg shows its OWN as-of time
 *      — a spec asserts the legs are NOT blended into one timestamp (ADR-0042).
 *   2. The Aave scenario shock control recomputes health factor + liquidation
 *      distance for a dialed shock (the request carries the shock).
 *   3. The LP scenario shock control recomputes impermanent loss.
 *   4. A conditional probability is rendered WITH its volatility assumption
 *      inline — a spec asserts no bare probability (ADR-0037).
 *   5. There is NO rebalance/exit/buy/sell control anywhere (ADR-0029 boundary).
 *   6. The Portfolio menu item mounts the view.
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen, waitFor } from '@testing-library/react'

// Keep the real ApiError + sanitizeApiErrorBody; stub only the network methods.
jest.mock('../api/client', () => {
  const actual = jest.requireActual('../api/client')
  return {
    __esModule: true,
    ...actual,
    api: { getPortfolio: jest.fn(), recomputeRisk: jest.fn() },
  }
})
// App mounts the SSE stream + chart on render; neither is relevant here.
jest.mock('../hooks/useEventStream', () => ({ useEventStream: () => undefined }))
jest.mock('./OhlcvView', () => ({ OhlcvView: () => <div data-testid="ohlcv-stub" /> }))

import { api } from '../api/client'
import { App } from '../App'
import type { PortfolioRiskResponse, PortfolioSurface } from '../schemas/portfolio'
import { PortfolioView } from './PortfolioView'

const getPortfolio = api.getPortfolio as jest.MockedFunction<typeof api.getPortfolio>
const recomputeRisk = api.recomputeRisk as jest.MockedFunction<typeof api.recomputeRisk>

const ADDRESS = '0x' + 'a'.repeat(40)

const SURFACE: PortfolioSurface = {
  summary: {
    holdings: [
      {
        symbol: 'BTC',
        venue: 'binance',
        quantity: 0.5,
        avg_cost: null,
        as_of: '2026-07-06T12:00:00Z',
        usd_value: 30500,
        pricing_source: 'yahoo:BTC-USD',
        kind: 'spot',
      },
      {
        symbol: 'AAPL',
        venue: 'manual',
        quantity: 100,
        avg_cost: 185.5,
        as_of: '2026-07-01T00:00:00Z',
        usd_value: 20000,
        pricing_source: 'yahoo:AAPL',
        kind: 'manual',
      },
    ],
    unrealized_pnl_usd: 1450,
    exposure_by_asset: { BTC: 30500, AAPL: 20000 },
    exposure_by_venue: { binance: 30500, manual: 20000 },
    legs_as_of: { binance: '2026-07-06T12:00:00Z', manual: '2026-07-01T00:00:00Z' },
    queried_at: '2026-07-21T00:00:00Z',
  },
  leg_errors: {},
  notes: [],
  error: null,
  message: null,
}

const AAVE_SCENARIO: PortfolioRiskResponse = {
  kind: 'scenario',
  aave: {
    account: {
      chain: 'base',
      total_collateral_base: 10000,
      total_debt_base: 4000,
      available_borrows_base: 2000,
      liquidation_threshold: 0.8,
      ltv: 0.7,
      health_factor: 1.8,
    },
    scenario: {
      collateral_shock: -0.5,
      collateral_value_before: 10000,
      collateral_value_after: 5000,
      debt_value: 4000,
      net_value_before: 6000,
      net_value_after: 1000,
      health_factor_before: 1.8,
      health_factor_after: 1.2,
      liquidation_distance_before: 0.44,
      liquidation_distance_after: 0.17,
    },
    error: null,
    message: null,
  },
  lp: null,
  disclaimer: 'Conditional facts about the position.',
}

const AAVE_CONDITIONAL: PortfolioRiskResponse = {
  kind: 'conditional',
  aave: {
    account: AAVE_SCENARIO.aave!.account,
    liquidation: {
      probability: 0.062,
      horizon_days: 30,
      liquidation_distance: 0.17,
      daily_vol: 0.031,
      seed: 0,
      assumption: 'trailing 90d realized vol of ETH',
    },
    error: null,
    message: null,
  },
  lp: null,
  disclaimer: 'Conditional facts about the position.',
}

const LP_SCENARIO: PortfolioRiskResponse = {
  kind: 'scenario',
  aave: null,
  lp: {
    value_before: 6000,
    hodl_value_after: 5200,
    lp_value_after: 5100,
    impermanent_loss: -0.024,
    error: null,
  },
  disclaimer: 'Conditional facts about the position.',
}

beforeEach(() => {
  getPortfolio.mockReset()
  recomputeRisk.mockReset()
  getPortfolio.mockResolvedValue(SURFACE)
  recomputeRisk.mockImplementation((req) => {
    if (req.kind === 'conditional') return Promise.resolve(AAVE_CONDITIONAL)
    if (req.lp) return Promise.resolve(LP_SCENARIO)
    return Promise.resolve(AAVE_SCENARIO)
  })
})

it('renders holdings with each venue leg carrying its OWN as-of (never blended)', async () => {
  render(<PortfolioView />)

  await waitFor(() =>
    expect(getPortfolio).toHaveBeenCalledWith({ wallet: undefined, includeDefiBasis: true }),
  )

  const rows = await screen.findAllByTestId('portfolio-holding-row')
  expect(rows).toHaveLength(2)
  expect(screen.getByText('BTC')).toBeInTheDocument()
  expect(screen.getByText('AAPL')).toBeInTheDocument()

  // Each leg keeps its own stamp — the two are DISTINCT (not one blended "now").
  const binanceAsOf = screen.getByTestId('portfolio-leg-asof-binance').textContent ?? ''
  const manualAsOf = screen.getByTestId('portfolio-leg-asof-manual').textContent ?? ''
  expect(binanceAsOf).toContain('2026-07-06 12:00')
  expect(manualAsOf).toContain('2026-07-01 00:00')
  expect(binanceAsOf).not.toEqual(manualAsOf)

  // Exposure + unrealized are surfaced.
  expect(screen.getByTestId('portfolio-summary')).toHaveTextContent('+$1,450.00')
})

it('recomputes Aave health factor + liquidation distance for a dialed shock', async () => {
  render(<PortfolioView />)
  await screen.findAllByTestId('portfolio-holding-row')

  fireEvent.change(screen.getByTestId('risk-aave-address'), { target: { value: ADDRESS } })
  fireEvent.change(screen.getByTestId('risk-aave-shock'), { target: { value: '-0.5' } })

  await waitFor(() =>
    expect(recomputeRisk).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: 'scenario',
        address: ADDRESS,
        chain: 'base',
        collateral_shock: -0.5,
      }),
    ),
  )
  const result = await screen.findByTestId('risk-aave-scenario')
  expect(result).toHaveTextContent('1.20') // health factor after
  expect(result).toHaveTextContent('17.00%') // liquidation distance after
})

it('recomputes impermanent loss for a dialed LP shock', async () => {
  render(<PortfolioView />)
  await screen.findAllByTestId('portfolio-holding-row')

  fireEvent.change(screen.getByTestId('risk-lp-shock'), { target: { value: '-0.5' } })

  await waitFor(() =>
    expect(recomputeRisk).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: 'scenario',
        lp: expect.objectContaining({ shock0: -0.5, shock1: 0 }),
      }),
    ),
  )
  expect(await screen.findByTestId('risk-lp-scenario')).toHaveTextContent('-2.40%')
})

it('renders a conditional probability WITH its volatility assumption (no bare number)', async () => {
  render(<PortfolioView />)
  await screen.findAllByTestId('portfolio-holding-row')

  fireEvent.change(screen.getByTestId('risk-aave-address'), { target: { value: ADDRESS } })
  fireEvent.click(screen.getByTestId('risk-aave-tab-conditional'))
  fireEvent.change(screen.getByTestId('risk-aave-symbol'), { target: { value: 'ETH' } })
  fireEvent.click(screen.getByTestId('risk-aave-probability'))

  const result = await screen.findByTestId('risk-aave-conditional')
  expect(result).toHaveTextContent('6.20%') // the probability
  // The assumption travels WITH the probability — never a bare number.
  expect(screen.getByTestId('risk-aave-assumption')).toHaveTextContent(
    'trailing 90d realized vol of ETH',
  )
})

it('carries NO rebalance / exit / buy / sell control anywhere (ADR-0029)', async () => {
  const { container } = render(<PortfolioView />)
  await screen.findAllByTestId('portfolio-holding-row')

  for (const button of screen.getAllByRole('button')) {
    expect(button.textContent ?? '').not.toMatch(/rebalance|\bbuy\b|\bsell\b|\bexit\b/i)
  }
  expect(container.textContent ?? '').not.toMatch(/rebalance|\bbuy\b|\bsell\b/i)
})

it('exposes a Portfolio menu item that mounts the view when selected', async () => {
  render(<App />)

  fireEvent.click(screen.getByTestId('nav-menu-trigger'))
  const item = screen.getByTestId('nav-portfolio')
  fireEvent.click(item)

  expect(await screen.findByRole('region', { name: 'Portfolio' })).toBeInTheDocument()
  await waitFor(() => expect(getPortfolio).toHaveBeenCalled())
})
