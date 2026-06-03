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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    # LP-only; `None` for non-LP positions and for LP positions whose source does
    # not expose tick boundaries (Zerion — see module docstring).
    pool: str | None = None
    tick_lower: int | None = None
    tick_upper: int | None = None
    in_range: bool | None = None

    @field_validator("usd_value")
    @classmethod
    def _usd_value_must_be_finite_and_non_negative(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("usd_value must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("usd_value must be non-negative")
        return v


__all__ = ["Chain", "DefiPosition", "PositionKind", "PositionToken"]
