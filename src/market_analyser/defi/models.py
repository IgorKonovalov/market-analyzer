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


__all__ = [
    "Chain",
    "DefiPosition",
    "LpPositionDetail",
    "PositionKind",
    "PositionToken",
]
