/**
 * Hand-authored renderer types for the DeFi wallet-P&L response (Plan 0088 phase 5).
 *
 * NOT generated into `types/sidecar/*`: the sidecar route (`POST /defi/pnl`)
 * serializes `positions` as `list[dict[str, Any]]`, so the OpenAPI schema can only
 * express it as an untyped object array — `gen-types` can't reach the nested
 * PositionPnl/WindowPnl shapes. These mirror the Pydantic models in
 * `src/market_analyser/defi/pnl.py` (widened by Plan 0088 phases 1–4) and are
 * consumed via `callJson<WalletPnlResponse>`, like every other typed sidecar call.
 */

/** The fixed rolling-window set (Plan 0088 / ADR-0082). */
export type PnlWindow = '7d' | '30d' | '90d' | 'all'

/** The column order the view renders — shortest to longest, `all` last. */
export const PNL_WINDOWS: readonly PnlWindow[] = ['7d', '30d', '90d', 'all']

export interface WindowPnl {
  window: PnlWindow
  /** EXACT realized P&L attributable to the events inside the window. */
  realized_usd: number
  /** ESTIMATED total return (realized + unrealized drift). `null` when the window
   * start couldn't be priced — an honest per-window gap, rendered as an em dash. */
  total_return_usd: number | null
  /** Always `true` — labels `total_return_usd` as an estimate. */
  estimated: boolean
}

export interface RewardAmount {
  symbol: string
  amount: number
  usd_value: number | null
}

export interface PositionPnl {
  position_id: string
  /** LP positions are the headline and are sorted first by the sidecar. */
  is_lp: boolean
  realized_usd: number | null
  unrealized_usd: number | null
  cost_basis_usd: number | null
  vs_hodl_usd: number | null
  incomplete: boolean
  notes: string[]
  windows: WindowPnl[]
  unclaimed_rewards: RewardAmount[] | null
}

export interface WalletPnlResponse {
  /** Masked (e.g. `0x1234…abcd`). */
  wallet: string
  positions: PositionPnl[]
  position_count: number
  incomplete: boolean
  /** True iff any position is incomplete; the wallet totals are then a partial
   * sum over the complete positions only (never null-everything). */
  partial: boolean
  incomplete_position_count: number
  realized_usd: number | null
  unrealized_usd: number | null
  unclaimed_rewards: RewardAmount[] | null
  crosscheck_zerion_total: number | null
  crosscheck_warning: boolean
}
