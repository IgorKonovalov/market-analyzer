"""Pydantic models that cross the data-layer boundary.

The bootstrap (Plan 0001 phase 2) only exercises `Bar`. The other models are
declared with minimum-viable shape so the Provider Protocol can be typed today;
each is filled out in the phase that earns its corresponding method.

Validation rules on `Bar` enforce the "no garbage past the boundary" contract
from `best-practices.md` — NaN, negative, or future bars are rejected at parse
time so downstream code (strategies, backtests, the chart) can trust the values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from market_analyser.data.errors import FailureReason


class Bar(BaseModel):
    """A single OHLCV bar. Boundary-validated; downstream code may trust the fields."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    event_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    source: str = Field(min_length=1)

    @field_validator("event_ts")
    @classmethod
    def _event_ts_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("event_ts must be timezone-aware (UTC)")
        return v.astimezone(UTC)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def _ohlc_must_be_finite_and_non_negative(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("OHLC values must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("OHLC values must be non-negative")
        return v

    @field_validator("volume")
    @classmethod
    def _volume_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("volume must be finite")
        return v

    @model_validator(mode="after")
    def _high_low_invariant(self) -> Bar:
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) < low ({self.low})")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open ({self.open}) outside [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close ({self.close}) outside [low, high]")
        return self


class Quote(BaseModel):
    """A point-in-time quote. Reserved for phase that implements get_quote."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    price: float
    as_of: datetime
    source: str = Field(min_length=1)


class SymbolInfo(BaseModel):
    """Symbol-search result. Reserved for phase that implements search_symbols."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    name: str
    exchange: str = ""


class ScreenerRow(BaseModel):
    """One row of screener output. Reserved for phase that implements get_screener."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    fields: dict[str, float | str | None] = Field(default_factory=dict)


class SentimentSample(BaseModel):
    """Aggregated sentiment reading for a symbol over a window (Plan 0010)."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    score: float
    window: str
    as_of: datetime
    source: str = Field(min_length=1)
    # {"positive": n, "negative": n, "neutral": n} over the scored items.
    breakdown: dict[str, int] = Field(default_factory=dict)


class NewsItem(BaseModel):
    """A single news item from the RSS news adapter (Plan 0010)."""

    model_config = ConfigDict(frozen=True)

    # "" = no symbol filter was applied (fetch(symbol=None)); otherwise the
    # applied filter, e.g. "BTC". Permits "" so the no-filter sentinel is
    # constructible; kept as `str` (not `str | None`) so downstream code does
    # not grow Optional-handling. See plan 0010 phase 1.
    symbol: str = ""
    title: str
    url: str
    published_at: datetime
    source: str = Field(min_length=1)
    summary: str = ""
    # VADER compound score over title + summary, in [-1.0, 1.0]; None unless the
    # adapter was asked for sentiment (fetch(with_sentiment=True)). Plan 0010 ph2.
    compound_sentiment: float | None = None


class MarketSentimentSample(BaseModel):
    """Market-wide sentiment (e.g. crypto Fear & Greed) — distinct from the
    per-symbol `SentimentSample` (Plan 0011).

    F&G is market-wide, not per-symbol; pretending it were a special "symbol"
    would be a category error, so it gets its own model and Protocol method
    rather than overloading `get_sentiment(symbol, ...)`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["crypto"]  # extends to "equity" when CNN equity F&G lands (additive)
    value: int = Field(ge=0, le=100)
    classification: Literal["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    published_at: datetime  # from the upstream timestamp, normalised to UTC
    source: str = Field(min_length=1)  # "alternative.me-fng"
    window: str = "current"  # always "current" in v1


@dataclass(frozen=True)
class Coverage:
    """Cache-only read result for backfill scheduling (Plan 0013): the bars
    already cached for a window, plus the gaps still needed to cover it. Computed
    WITHOUT any upstream fetch — a plain carrier, not a boundary-validated model
    (the bars it holds were already validated when they entered the cache)."""

    cached: list[Bar]
    gaps: list[tuple[datetime, datetime]]


@dataclass(frozen=True)
class BackfillResult:
    """Result of a fetch-on-miss that surfaces partial failures instead of raising
    (Plan 0013). `bars` is the merged cache+fetched set so far; `partial_reason`
    is `None` on full success, or the typed reason when some (but not all) gaps
    failed; `message` carries the upstream detail for the agent."""

    bars: list[Bar]
    partial_reason: FailureReason | None
    message: str | None


__all__ = [
    "BackfillResult",
    "Bar",
    "Coverage",
    "MarketSentimentSample",
    "NewsItem",
    "Quote",
    "ScreenerRow",
    "SentimentSample",
    "SymbolInfo",
]
