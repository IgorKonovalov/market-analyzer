"""Unified cross-venue holdings models (Plan 0041 phase 2; ADR-0042).

`Holding` is the shape every venue leg normalizes into — Binance spot/futures,
DeFi positions, manual file entries. Two provenance rules are structural here,
not conventions:

- **Freshness is never blended.** Every holding carries its own `as_of` (the
  venue leg's read instant, or the manual file's user-maintained stamp). There
  is no single implied "now" — a stale manual row stays visibly stale next to
  a live Binance read (the ADR-0042 negative-consequence mitigation).
- **No single-oracle pretense.** A USD valuation never appears without the
  reference that produced it: `usd_value` and `pricing_source` are paired by a
  model validator — both set, or both absent. The three legs price differently
  (Binance mark, DefiLlama, the OHLCV provider); each holding names its own.

`avg_cost` is the average-cost basis per unit — ADR-0036's method, adopted
venue-wide by ADR-0042 so the DeFi leg agrees with the CEX/manual legs by
construction. `None` means honestly unknown (e.g. a manual entry whose cost
the user omitted), never zero.

These are facts-only carriers: no recommendation-shaped field exists or may be
added (ADR-0029 — the advisor is the one sanctioned crossing).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Venue = Literal["binance", "defi", "manual"]

VENUES: tuple[Venue, ...] = ("binance", "defi", "manual")


class Holding(BaseModel):
    """One unified holding — a venue-scoped quantity of one asset, with its
    own cost basis, freshness stamp, and (when priced) named pricing reference.
    Boundary-validated; downstream aggregation may trust the fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)  # venue-native asset/contract name
    venue: Venue
    quantity: float  # signed: negative = short (futures) / liability; never zero
    avg_cost: float | None = None  # average-cost basis per unit (ADR-0036); None = unknown
    as_of: datetime  # this leg's freshness — never blended across venues
    usd_value: float | None = None  # signed venue valuation; None = unpriced
    pricing_source: str | None = None  # which reference priced usd_value; None = unpriced
    kind: str = "spot"  # "spot" | "futures" | "defi:<position kind>" | "manual"

    @field_validator("quantity")
    @classmethod
    def _quantity_must_be_finite_and_nonzero(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("quantity must be finite (no NaN/Inf)")
        if v == 0:
            raise ValueError("a holding's quantity must be nonzero")
        return v

    @field_validator("avg_cost")
    @classmethod
    def _avg_cost_must_be_finite_and_non_negative(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not math.isfinite(v):
            raise ValueError("avg_cost must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("avg_cost must be non-negative")
        return v

    @field_validator("usd_value")
    @classmethod
    def _usd_value_must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("usd_value must be finite (no NaN/Inf)")
        return v

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _valuation_carries_its_reference(self) -> Holding:
        if (self.usd_value is None) != (self.pricing_source is None):
            raise ValueError(
                "usd_value and pricing_source are paired provenance: "
                "a valuation never appears without the reference that priced it",
            )
        return self


class PortfolioSummary(BaseModel):
    """The unified cross-venue view `portfolio_summary` returns (Plan 0041
    phase 3): holdings + average-cost basis + unrealized P&L + exposure, each
    leg stamped with its own as-of time.

    `unrealized_pnl_usd` is `None` when no leg could contribute a priced,
    basis-bearing figure — honestly unknown, never a confident zero.
    `legs_as_of` carries one stamp per venue leg that produced data; a leg
    with nothing to report is absent, not defaulted. `queried_at` is the
    aggregation instant (provenance only — no computation reads it).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    holdings: list[Holding]
    unrealized_pnl_usd: float | None
    exposure_by_asset: dict[str, float]  # USD by asset symbol, priced legs only
    exposure_by_venue: dict[str, float]  # USD by venue, priced legs only
    legs_as_of: dict[str, datetime]  # per-venue freshness — never blended
    queried_at: datetime

    @field_validator("unrealized_pnl_usd")
    @classmethod
    def _pnl_must_be_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("unrealized_pnl_usd must be finite (no NaN/Inf)")
        return v

    @field_validator("exposure_by_asset", "exposure_by_venue")
    @classmethod
    def _exposures_must_be_finite(cls, v: dict[str, float]) -> dict[str, float]:
        for key, value in v.items():
            if not math.isfinite(value):
                raise ValueError(f"exposure for {key!r} must be finite (no NaN/Inf)")
        return v

    @field_validator("queried_at")
    @classmethod
    def _queried_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("queried_at must be timezone-aware (UTC)")
        return v.astimezone(UTC)

    @field_validator("legs_as_of")
    @classmethod
    def _legs_as_of_must_be_utc(cls, v: dict[str, datetime]) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for leg, stamp in v.items():
            if stamp.tzinfo is None:
                raise ValueError(f"legs_as_of[{leg!r}] must be timezone-aware (UTC)")
            out[leg] = stamp.astimezone(UTC)
        return out


__all__ = ["VENUES", "Holding", "PortfolioSummary", "Venue"]
