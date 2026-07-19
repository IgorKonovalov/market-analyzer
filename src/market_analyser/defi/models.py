"""Normalized DeFi position model (ADR-0035, Plan 0032 phase 2).

`DefiPosition` is the one shape every downstream DeFi concern consumes — the
discovery service that emits it (phase 3), the agent-facing `scan_wallet` tool
(phase 4), and the later P&L / risk engines. It is the *interpreted* position
(an Aave supply, a Uniswap-v3 LP, an Aerodrome LP), not a raw token balance.

Boundary-validated like `Bar` (`data/types.py`): `usd_value` is finite and
non-negative and each token `amount` is finite and positive, so a NaN / Inf /
negative measurement is rejected at construction rather than silently coerced to
zero (best-practices.md "no garbage past the boundary"; ADR-0035). Downstream
code may trust the fields.

The LP-only `tick_lower` / `tick_upper` / `in_range` are carried as `| None`:
the discovery source for this plan (Zerion, ADR-0034) surfaces *interpreted*
positions but **not** Uniswap-v3 tick boundaries — those are on-chain NFT state
that the deep-adapter plan reads via RPC / The Graph ("What this plan does NOT
do"). The fields exist so that future source populates them without a schema
change; here they stay `None`.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The EVM majors this plan targets (ADR-0034). A position on any other chain is
# dropped by the adapter rather than widened into the model.
Chain = Literal["ethereum", "base", "arbitrum", "optimism"]

# The interpreted position kinds. `lp` = liquidity-pool position; the two
# `lending_*` split a money-market position into supply vs borrow; `staking`
# covers staked/locked single-asset positions.
PositionKind = Literal["lp", "lending_supply", "lending_borrow", "staking"]


class PositionToken(BaseModel):
    """One underlying token of a position: its symbol, on-chain address, and the
    held amount. `amount` is finite and strictly positive — a zero/NaN/negative
    quantity is a malformed position, not a token worth carrying."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    address: str = Field(min_length=1)
    amount: float = Field(gt=0)

    @field_validator("amount")
    @classmethod
    def _amount_must_be_finite(cls, v: float) -> float:
        # `gt=0` already rejects NaN and negatives; this also rejects +Inf.
        if not math.isfinite(v):
            raise ValueError("token amount must be finite (no NaN/Inf)")
        return v


class DefiPosition(BaseModel):
    """A single interpreted DeFi position. Boundary-validated; trusted downstream."""

    model_config = ConfigDict(frozen=True)

    position_id: str = Field(min_length=1)  # stable: chain + protocol + pool/nft group
    chain: Chain
    protocol: str = Field(min_length=1)  # "aave-v3" | "uniswap-v3" | "aerodrome" | …
    kind: PositionKind
    tokens: list[PositionToken] = Field(min_length=1)
    usd_value: float  # current position value; boundary-validated below

    # The on-chain pool/pair contract address (`0x…`) Zerion exposes on every
    # complex position (28/28 in the capability survey). It is the discovery→deep
    # join key: the deep adapter (Plan 0034 phases 3-4) keys its RPC / The-Graph
    # read on it, and the enrichment step (phase 5) matches an `LpPositionDetail`
    # back to the `DefiPosition` it enriches by it. `None` for positions whose
    # source does not expose it (e.g. single-asset staking). Validated non-empty.
    pool_address: str | None = Field(default=None, min_length=1)

    # LP-only; `None` for non-LP positions and for LP positions whose source does
    # not expose the on-chain detail. The discovery source (Zerion) leaves them
    # `None`; the deep adapter (Plan 0034 phases 3-4) fills them via RPC / The
    # Graph and the enrichment step (phase 5) folds them onto the position.
    pool: str | None = None
    tick_lower: int | None = None
    tick_upper: int | None = None
    in_range: bool | None = None
    current_tick: int | None = None
    uncollected_fees: list[PositionToken] | None = None

    @field_validator("usd_value")
    @classmethod
    def _usd_value_must_be_finite_and_non_negative(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("usd_value must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("usd_value must be non-negative")
        return v


class LpPositionDetail(BaseModel):
    """The deep on-chain state of a single concentrated-liquidity LP position
    (Uniswap-v3 / Aerodrome Slipstream), produced by an `LpPositionDetailSource`
    (Plan 0034 / 0048). It *enriches* the `DefiPosition` discovery returns: the
    precise tick range, where the pool's current tick sits relative to it (in-range
    status), and the fees accrued but not yet collected.

    **`uncollected_fees` definition (Plan 0048).** These are the position struct's
    `tokensOwed0` / `tokensOwed1` words read *as-is* — claimed-but-not-yet-withdrawn
    **swap fees**, scaled by each token's decimals. They are *not* recomputed from
    `feeGrowthInside` deltas, so they **under-report** real-time accrued fees: a CL
    position's owed words only update on a poke/collect and read `0` in between (the
    2026-06-05 smoke read `0` for an in-range staked position). This is the cheap,
    deterministic definition; the accurate `feeGrowthInside` computation was the
    rejected alternative. For a *staked* CL position, gauge **emissions** are a
    separate reward stream and are deliberately **out of scope** here — this field
    is swap fees only.

    Boundary-validated in the model's house style: ticks are finite ints with
    `tick_lower < tick_upper`, `in_range` is required to agree with the half-open
    range `tick_lower <= current_tick < tick_upper` (a mismatch is a decode bug,
    rejected at construction, not silently trusted), and each uncollected-fee
    entry is a `PositionToken` (finite, positive amount). No owed fees is an empty
    list, not `None`. Downstream code (enrichment, later risk) may trust it."""

    model_config = ConfigDict(frozen=True)

    tick_lower: int
    tick_upper: int
    current_tick: int
    in_range: bool  # must equal tick_lower <= current_tick < tick_upper
    uncollected_fees: list[PositionToken]

    @model_validator(mode="after")
    def _ticks_ordered_and_in_range_consistent(self) -> LpPositionDetail:
        if self.tick_lower >= self.tick_upper:
            raise ValueError("tick_lower must be strictly less than tick_upper")
        expected = self.tick_lower <= self.current_tick < self.tick_upper
        if self.in_range != expected:
            raise ValueError(
                "in_range must equal (tick_lower <= current_tick < tick_upper)",
            )
        return self


class AaveAccountDetail(BaseModel):
    """Aave v3 aggregate account health for one `(wallet, chain)`, read on-chain via
    `Pool.getUserAccountData(user)` (Plan 0042 phase 1 / ADR-0037, ADR-0034) — the
    lending *depth* that discovery does not expose and that the scenario engine needs
    to recompute a health factor + liquidation distance under a supplied collateral
    shock. It is a per-`(wallet, chain)` **aggregate** fact, not a per-position fold:
    `getUserAccountData` returns the account-wide totals, so one detail summarises all
    of a wallet's Aave supply/borrow on a chain.

    `total_collateral_base` / `total_debt_base` / `available_borrows_base` are Aave's
    base-currency amounts (USD on the target markets) already scaled to float USD.
    `liquidation_threshold` and `ltv` are fractions in `[0, 1]` (Aave returns basis
    points; scaled here). `health_factor` is the collateral-weighted ratio, or `None`
    for a **no-debt** account (Aave returns `type(uint256).max` when there is no debt —
    an undefined HF, carried as `None`, never a fabricated number).

    Boundary-validated in the `DefiPosition` house style: every measurement is finite,
    the USD amounts and the two fractions are non-negative, and a present
    `health_factor` is finite and strictly positive. Downstream (the scenario engine)
    may trust the fields."""

    model_config = ConfigDict(frozen=True)

    chain: Chain
    total_collateral_base: float = Field(ge=0)  # USD (base currency)
    total_debt_base: float = Field(ge=0)  # USD
    available_borrows_base: float = Field(ge=0)  # USD
    liquidation_threshold: float = Field(ge=0)  # fraction, currentLiquidationThreshold / 1e4
    ltv: float = Field(ge=0)  # fraction, ltv / 1e4
    health_factor: float | None = None  # WAD / 1e18; None when the account has no debt
    as_of: datetime

    @field_validator(
        "total_collateral_base",
        "total_debt_base",
        "available_borrows_base",
        "liquidation_threshold",
        "ltv",
    )
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Aave account measurement must be finite (no NaN/Inf)")
        return v

    @field_validator("health_factor")
    @classmethod
    def _health_factor_finite_and_positive(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("health_factor must be finite (no NaN/Inf)")
        if v <= 0:
            raise ValueError("health_factor must be strictly positive when present")
        return v


class RewardAmount(BaseModel):
    """One reward token currently owed-but-unclaimed to a position (Plan 0084 /
    ADR-0079), read on-chain via the gauge's `earned()`.

    This is a labeled **current-state** reading, not a replay-derived figure: it
    has no claim transaction to replay (tx-replay is structurally blind to it), so
    it is kept out of realized/unrealized P&L and out of the deterministic
    byte-identical guarantee — the same category as discovery's live `usd_value`
    (ADR-0036). `usd_value` is the reward's value at the **current** price
    (provenance: not block-time), `None` when it cannot be priced — honest, never
    a zeroed stand-in.

    Boundary-validated in the house style: `amount` is finite and strictly
    positive (a zero/NaN owed amount is not a reward worth carrying), and
    `usd_value`, when present, is finite and non-negative."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    amount: float = Field(gt=0)
    usd_value: float | None = None

    @field_validator("amount")
    @classmethod
    def _amount_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("reward amount must be finite (no NaN/Inf)")
        return v

    @field_validator("usd_value")
    @classmethod
    def _usd_value_must_be_finite_and_non_negative(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("reward usd_value must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("reward usd_value must be non-negative")
        return v


class ExecutableQuote(BaseModel):
    """One DEX pool's **executable** price for a canonical pair at a specific size,
    produced by an `ExecutableQuoteSource` (Plan 0086 / ADR-0080) — the input the
    cross-pool discrepancy screener v2 compares across venues.

    It carries the **already-net** cost of a real round-trip leg at `trade_size`
    (rather than a marginal/spot price the screener must add an estimated cost to):

    - **`buy_cost`** — quote-token **in** to ACQUIRE `trade_size` base at this pool
      (an exact-output swap), net of the pool's fee and its slippage for the size;
    - **`sell_proceeds`** — quote-token **out** from SELLING `trade_size` base at
      this pool (an exact-input swap), net of the pool's fee and slippage.

    Constant-product pools compute these from `x·y=k`; concentrated-liquidity pools
    from the DEX Quoter (ADR-0080). The screener needs no cost model of its own — it
    ranks pre-costed quotes: `net = max(sell_proceeds) - min(buy_cost) - gas`.

    **`marginal_price`** is the pool's zero-size reference (quote-per-base, from
    `getReserves`/`slot0`), carried **only** so the screener can reconstruct a
    slippage/fee breakdown for auditability (Plan 0079 honesty pin). That breakdown
    is *derived* from the marginal reference, **not a second source of truth** — the
    executable `buy_cost`/`sell_proceeds` are authoritative (ADR-0080).

    **`fee_tier`** is the pool's fee in basis points — the CL tier (500/3000/10000)
    or the CP pool fee — used only to split the reconstructed breakdown into a fee
    vs slippage share; `None` when a source cannot attribute a tier.

    Boundary-validated in the house style (`DefiPosition`): every measurement is
    finite, `buy_cost` / `sell_proceeds` / `marginal_price` / `trade_size` strictly
    positive — a NaN / Inf / non-positive quote is rejected at construction, never
    silently zeroed (ADR-0035, best-practices "no garbage past the boundary").
    Downstream code may trust the fields."""

    model_config = ConfigDict(frozen=True)

    pool_id: str = Field(min_length=1)  # pool contract address (0x…)
    dex: str = Field(min_length=1)  # "aerodrome" | "uniswap-v3" | "aerodrome-slipstream" | …
    chain: Chain
    pair: str = Field(min_length=1)  # canonical "BASE/QUOTE", e.g. "WETH/USDC"
    fee_tier: int | None = Field(default=None, ge=0)  # fee in bps; None if unattributed
    trade_size: float = Field(gt=0)  # base-token size the quote is priced for
    buy_cost: float = Field(gt=0)  # quote-in to ACQUIRE trade_size base (exact-output), net
    sell_proceeds: float = Field(gt=0)  # quote-out from SELLING trade_size base (exact-in), net
    marginal_price: float = Field(gt=0)  # zero-size reference (quote-per-base) — for the breakdown
    as_of: datetime  # read time (provenance)

    @field_validator("trade_size", "buy_cost", "sell_proceeds", "marginal_price")
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        # `gt=0` already rejects NaN and negatives; this also rejects +Inf.
        if not math.isfinite(v):
            raise ValueError("executable-quote measurement must be finite (no NaN/Inf)")
        return v


class FundamentalsPoint(BaseModel):
    """One `(date, value)` sample in a DeFi fundamentals time-series — a point on
    the TVL history a `DefiFundamentalsSource` returns (Plan 0107 / ADR-0102).

    `date` is a UTC epoch-second timestamp (DefiLlama's `tvl[].date` currency);
    `value` is finite and non-negative USD. Boundary-validated in the house style:
    a NaN / Inf / negative measurement is rejected at construction, never coerced."""

    model_config = ConfigDict(frozen=True)

    date: int
    value: float = Field(ge=0)

    @field_validator("value")
    @classmethod
    def _value_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("fundamentals point value must be finite (no NaN/Inf)")
        return v


class VolumeSummary(BaseModel):
    """Rolling DEX-volume windows in USD (Plan 0107) — DefiLlama's
    `total24h/7d/30d` for a protocol, each `| None` where the upstream omits the
    window. `change_1d_pct` is the signed percent change DefiLlama reports. Each
    present value is finite and non-negative (volume cannot be negative)."""

    model_config = ConfigDict(frozen=True)

    volume_24h: float | None = Field(default=None, ge=0)
    volume_7d: float | None = Field(default=None, ge=0)
    volume_30d: float | None = Field(default=None, ge=0)
    change_1d_pct: float | None = None

    @field_validator("volume_24h", "volume_7d", "volume_30d")
    @classmethod
    def _volume_must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("volume must be finite (no NaN/Inf)")
        return v


class UnlockEvent(BaseModel):
    """One token unlock / emission event on a protocol's dilution calendar
    (Plan 0107) — when, how many tokens, and (best-effort) the USD value at
    DefiLlama's reference price. Present only where DefiLlama covers the
    emissions-unlocks dataset for the protocol; the keyless `/emission/{slug}`
    endpoint is frequently Pro-gated (AERO returns HTTP 402), in which case the
    calendar degrades to an honest "not covered" note rather than a fabricated
    schedule (ADR-0102 risk #1). `tokens` is finite and non-negative."""

    model_config = ConfigDict(frozen=True)

    date: int  # epoch seconds
    tokens: float = Field(ge=0)
    usd_value: float | None = None
    category: str | None = None  # e.g. "publicSale" | "team" | "liquidity"

    @field_validator("tokens")
    @classmethod
    def _tokens_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("unlock token amount must be finite (no NaN/Inf)")
        return v


class EmissionsDetail(BaseModel):
    """Aerodrome-native weekly-emission snapshot (Plan 0107 phase 4 / ADR-0102),
    read from the Minter contract over the Base RPC — the deep-tier fact DefiLlama
    does not expose: the current epoch's AERO emission and its decay. Populated
    **only** for Aerodrome-on-Base; `None` at the DefiLlama tier.

    `weekly_emission` is AERO tokens this epoch (scaled from wei, strictly
    positive). `weekly_decay_pct` is the per-epoch decay in percent derived from the
    Minter's `WEEKLY_DECAY` basis-points constant (9900 → 1.0%/epoch), best-effort
    `None` if that read fails. `epoch` is the Minter's `epochCount`;
    `tail_emission_rate` is the raw `tailEmissionRate` (relevant once the schedule
    reaches tail emission). Boundary-validated: every present number is finite."""

    model_config = ConfigDict(frozen=True)

    weekly_emission: float = Field(gt=0)  # AERO tokens emitted this epoch
    weekly_decay_pct: float | None = Field(default=None, ge=0)  # % decay per epoch
    epoch: int | None = Field(default=None, ge=0)
    tail_emission_rate: float | None = Field(default=None, ge=0)

    @field_validator("weekly_emission", "weekly_decay_pct", "tail_emission_rate")
    @classmethod
    def _must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("emissions measurement must be finite (no NaN/Inf)")
        return v


class VeGaugeStats(BaseModel):
    """Aerodrome veAERO + Voter snapshot (Plan 0107 phase 4 / ADR-0102), read from
    the VotingEscrow + Voter contracts over the Base RPC — the ve-lock and
    vote-weight facts that drive AERO's emissions distribution. Populated **only**
    for Aerodrome-on-Base; `None` at the DefiLlama tier.

    `ve_total_locked` is the total AERO locked in the VotingEscrow (`supply()`,
    scaled from wei); `ve_total_voting_power` is the current total veAERO voting
    power (`totalSupply()`, which decays with time); `total_vote_weight` is the
    Voter's aggregate gauge vote weight (`totalWeight()`). Each is best-effort
    `None` when its read fails; every present number is finite and non-negative."""

    model_config = ConfigDict(frozen=True)

    ve_total_locked: float | None = Field(default=None, ge=0)  # AERO locked in ve
    ve_total_voting_power: float | None = Field(default=None, ge=0)  # veAERO (decaying)
    total_vote_weight: float | None = Field(default=None, ge=0)  # Voter.totalWeight()

    @field_validator("ve_total_locked", "ve_total_voting_power", "total_vote_weight")
    @classmethod
    def _must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("ve/gauge measurement must be finite (no NaN/Inf)")
        return v


class DefiFundamentals(BaseModel):
    """DeFi-native token/protocol fundamentals as a **condition read** (Plan 0107 /
    ADR-0102) — the fundamentals surface that price/structure is blind to for a
    small-cap DeFi token: TVL + short history, DEX volume, fee/reward APR, token
    mcap/FDV, and the unlock/dilution calendar. Conditions only (ADR-0029): the
    model carries **no** `action`/`signal`/`recommendation` field, by design.

    Every substantive field is `| None` and **honest-null** on miss: a field the
    source cannot cover returns `None` with a `notes` entry naming the gap, never a
    zero or a fabricated value (ADR-0019). `notes` is the running provenance /
    coverage log; `source` names the primary tier ("defillama"); `as_of` is the
    read (wall-clock) time — these reads are wall-clock-sensitive with **no `as_of`
    historical replay** (ADR-0102), and each figure carries the upstream's own
    recency in `notes` where relevant.

    The Aerodrome-native deep tier (Plan 0107 phases 4-5) folds its
    emission-decay + veAERO/gauge fields onto this model additively; at the
    DefiLlama tier those deep fields are absent."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1)  # the echoed input symbol/protocol
    protocol_slug: str | None = None  # resolved DefiLlama slug (provenance)
    tvl: float | None = Field(default=None, ge=0)
    tvl_trend: list[FundamentalsPoint] | None = None
    dex_volume: VolumeSummary | None = None
    fee_apr: float | None = None  # annualized %, TVL-weighted across the protocol's pools
    reward_apr: float | None = None  # annualized %, emissions/reward APR
    mcap: float | None = Field(default=None, ge=0)  # circulating market cap, USD
    fdv: float | None = Field(default=None, ge=0)  # fully-diluted valuation, USD
    unlocks: list[UnlockEvent] | None = None
    # Deep-tier fields (Plan 0107 phases 4-5): the Aerodrome-native reader folds
    # these onto the DefiLlama payload for Aerodrome-on-Base; honest-null elsewhere.
    emissions_detail: EmissionsDetail | None = None
    ve_gauge: VeGaugeStats | None = None
    as_of: datetime
    source: str = Field(default="defillama", min_length=1)
    notes: list[str] = Field(default_factory=list)

    @field_validator("tvl", "mcap", "fdv")
    @classmethod
    def _usd_must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("USD measurement must be finite (no NaN/Inf)")
        return v

    @field_validator("fee_apr", "reward_apr")
    @classmethod
    def _apr_must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("APR must be finite (no NaN/Inf)")
        return v


__all__ = [
    "AaveAccountDetail",
    "Chain",
    "DefiFundamentals",
    "DefiPosition",
    "EmissionsDetail",
    "ExecutableQuote",
    "FundamentalsPoint",
    "LpPositionDetail",
    "PositionKind",
    "PositionToken",
    "RewardAmount",
    "UnlockEvent",
    "VeGaugeStats",
    "VolumeSummary",
]
