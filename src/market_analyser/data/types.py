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
    """A point-in-time live quote for one symbol (Plan 0019).

    `as_of` is the quote's own upstream timestamp (Yahoo `regularMarketTime`),
    not the anti-lookahead replay seam — `get_quote` rejects an `as_of` *argument*
    because a live quote has no replayable history (the provider raises there).

    The fields below `source` are additive (Plan 0019) and all optional/defaulted,
    so the bootstrap's price-only constructions still parse. They are derived from
    Yahoo's `/v8/finance/chart` `meta` block; any the upstream omits stay `None`/"".
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    price: float
    as_of: datetime
    source: str = Field(min_length=1)
    # --- Plan 0019: additive live-quote fields (all optional / defaulted) ---
    change_pct: float | None = None  # derived from previous_close, not Yahoo's field
    previous_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    currency: str = ""
    market_state: str = ""  # REGULAR | PRE | POST | CLOSED
    volume: float | None = None


class SymbolInfo(BaseModel):
    """Symbol-search result (Plan 0024). `symbol` is in Yahoo's native namespace
    and is therefore directly fetchable by `get_ohlcv` — the suggestion and fetch
    namespaces are the same set by construction (ADR-0026)."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)  # Yahoo-native, fetchable by get_ohlcv — e.g. "BTC-USD"
    name: str  # Yahoo longname / shortname / symbol fallback
    exchange: str = ""  # Yahoo exchDisp / exchange display string — e.g. "CCC", "NASDAQ", "CME"
    quote_type: str = ""  # Yahoo typeDisp / quoteType — e.g. "Cryptocurrency", "Equity", "ETF"


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


# The crypto macro regime vocabulary (Plan 0022 / ADR-0027): a fixed, closed,
# neutral four-value set naming a *structural condition* (where capital is
# sitting), never an action or risk grade. The classification rule lives in the
# CoinGecko adapter; this is the type the data layer and consumers switch on.
CryptoRegime = Literal["btc_led", "alt_structure", "risk_off_structure", "neutral"]


class MacroContext(BaseModel):
    """A single-call crypto macro read (Plan 0022 / ADR-0027): BTC price + 24h
    change, BTC dominance, total market cap + 24h change, plus a neutral
    structural `regime` descriptor.

    `as_of` is the upstream snapshot timestamp (CoinGecko `/global` `updated_at`,
    epoch seconds) normalised to UTC — the read is wall-clock-current, so
    `get_macro_context` rejects an `as_of` *argument* (there is no replayable
    history; the provider raises there). `regime` is a structural condition, not
    a recommendation — see ADR-0027 for the closed vocabulary and the invariants
    pinned by tests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["crypto"]  # extends additively if a non-crypto macro read lands
    btc_price: float = Field(gt=0)
    btc_change_24h: float
    btc_dominance_pct: float = Field(ge=0, le=100)
    total_market_cap_usd: float = Field(gt=0)
    total_market_cap_change_24h: float
    regime: CryptoRegime
    as_of: datetime
    source: str = Field(min_length=1)  # "coingecko"

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return v.astimezone(UTC)

    @field_validator(
        "btc_price", "btc_change_24h", "total_market_cap_usd", "total_market_cap_change_24h"
    )
    @classmethod
    def _measurements_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("macro measurements must be finite (no NaN/Inf)")
        return v


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
    "CryptoRegime",
    "MacroContext",
    "MarketSentimentSample",
    "NewsItem",
    "Quote",
    "ScreenerRow",
    "SentimentSample",
    "SymbolInfo",
]
