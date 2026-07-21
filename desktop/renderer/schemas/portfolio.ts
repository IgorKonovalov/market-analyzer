/**
 * Zod schemas for the portfolio surface (Plan 0043 phase 2).
 *
 * Two route payloads are validated at the renderer boundary before they reach
 * any view state (the plan's done-when: route payloads Zod-validated, the SSE
 * analogue for this pull-shaped surface):
 *
 *   - `GET /portfolio` → `portfolioSurfaceSchema`, `satisfies`-pinned to the
 *     generated `PortfolioSurfaceResponse` TS mirror so the compiler rejects any
 *     drift from the sidecar's OpenAPI (the `recommendation.ts` precedent).
 *   - `POST /portfolio/risk` → `portfolioRiskSchema`. The sidecar returns this
 *     leg as an untyped dict (intentionally — it mirrors the `defi_risk` MCP
 *     tool's own contract), so the renderer owns its runtime shape here; the TS
 *     types are `z.infer`red from the schema.
 *
 * Every conditional (`kind="conditional"`) leg carries its volatility
 * `assumption` + `daily_vol` inline — the schema requires them, so a bare
 * probability without its assumption is malformed by definition and never
 * reaches the panel (ADR-0037).
 */
import { z } from 'zod'

import type { PortfolioSurfaceResponse } from '../types/sidecar/portfolio-surface-response'

// ── GET /portfolio ────────────────────────────────────────────────────────────

const holdingSchema = z.object({
  symbol: z.string(),
  venue: z.enum(['binance', 'defi', 'manual']),
  quantity: z.number(),
  avg_cost: z.number().nullish(),
  as_of: z.string(),
  usd_value: z.number().nullish(),
  pricing_source: z.string().nullish(),
  kind: z.string().optional(),
})

const portfolioSummarySchema = z.object({
  holdings: z.array(holdingSchema),
  unrealized_pnl_usd: z.number().nullable(),
  exposure_by_asset: z.record(z.number()),
  exposure_by_venue: z.record(z.number()),
  // Per-venue freshness — never blended into one timestamp (ADR-0042).
  legs_as_of: z.record(z.string()),
  queried_at: z.string(),
})

export const portfolioSurfaceSchema = z.object({
  summary: portfolioSummarySchema,
  leg_errors: z.record(z.string()),
  notes: z.array(z.string()),
  error: z.string().nullish(),
  message: z.string().nullish(),
}) satisfies z.ZodType<PortfolioSurfaceResponse>

// ── POST /portfolio/risk ──────────────────────────────────────────────────────

const aaveAccountSchema = z.object({
  chain: z.string(),
  total_collateral_base: z.number(),
  total_debt_base: z.number(),
  available_borrows_base: z.number(),
  liquidation_threshold: z.number(),
  ltv: z.number(),
  health_factor: z.number(),
})

const aaveScenarioSchema = z.object({
  collateral_shock: z.number(),
  collateral_value_before: z.number(),
  collateral_value_after: z.number(),
  debt_value: z.number(),
  net_value_before: z.number(),
  net_value_after: z.number(),
  health_factor_before: z.number(),
  health_factor_after: z.number(),
  // Fractional collateral drop that reaches HF=1; null when the account carries
  // no debt (nothing to be liquidated against).
  liquidation_distance_before: z.number().nullable(),
  liquidation_distance_after: z.number().nullable(),
})

const aaveLiquidationSchema = z.object({
  probability: z.number(),
  horizon_days: z.number(),
  liquidation_distance: z.number(),
  daily_vol: z.number(),
  seed: z.number(),
  // The volatility model, stated inline — never a bare probability (ADR-0037).
  assumption: z.string(),
})

const aaveLegSchema = z.object({
  account: aaveAccountSchema.nullable(),
  scenario: aaveScenarioSchema.optional(),
  liquidation: aaveLiquidationSchema.nullish(),
  note: z.string().optional(),
  error: z.string().nullish(),
  message: z.string().nullish(),
})

const lpScenarioSchema = z.object({
  value_before: z.number(),
  hodl_value_after: z.number(),
  lp_value_after: z.number(),
  impermanent_loss: z.number(),
  error: z.string().nullish(),
})

const lpConditionalSchema = z.object({
  // Quantile map keyed `p5` / `p50` / `p95` (a percentile → IL fraction).
  quantiles: z.record(z.number()),
  mean: z.number(),
  horizon_days: z.number(),
  daily_vol: z.number(),
  seed: z.number(),
  assumption: z.string(),
  error: z.string().nullish(),
})

export const portfolioRiskSchema = z.object({
  kind: z.enum(['scenario', 'conditional']),
  aave: aaveLegSchema.nullable(),
  // A scenario-shaped or conditional-shaped LP leg, discriminated by `kind`
  // upstream; the union validates whichever the caller asked for.
  lp: z.union([lpScenarioSchema, lpConditionalSchema]).nullable(),
  disclaimer: z.string(),
})

export type Holding = z.infer<typeof holdingSchema>
export type PortfolioSummary = z.infer<typeof portfolioSummarySchema>
export type PortfolioSurface = z.infer<typeof portfolioSurfaceSchema>
export type AaveRiskLeg = z.infer<typeof aaveLegSchema>
export type LpScenarioLeg = z.infer<typeof lpScenarioSchema>
export type LpConditionalLeg = z.infer<typeof lpConditionalSchema>
export type PortfolioRiskResponse = z.infer<typeof portfolioRiskSchema>

/** `POST /portfolio/risk` request body — mirrors the sidecar `DefiRiskInput`.
 * At least one leg (an Aave account via `address`+`chain`, or an `lp` block) is
 * required; the sidecar 422s otherwise. */
export interface RiskRequest {
  kind: 'scenario' | 'conditional'
  address?: string
  chain?: 'ethereum' | 'base'
  collateral_shock?: number
  collateral_symbol?: string
  lp?: {
    amount0?: number
    price0?: number
    shock0?: number
    amount1?: number
    price1?: number
    shock1?: number
    ratio_log_returns?: number[]
  }
  horizon_days?: number
  seed?: number
  lookback_days?: number
}
