"""Metric-series id registry + the `MetricPoint` boundary model (ADR-0051).

Series ids are namespaced strings (`fng.value`, `coingecko.btc_dominance`, ...)
registered in the one module-level registry below. **The registry is the
schema**: the `metric_points` table is generic, so an unregistered (typo'd,
not-yet-landed) id must fail loudly at the repository boundary instead of
silently growing an orphan series. Plans 0056/0057 add their series here as
plain dict entries — no migration, no schema work.

The registry doubles as provenance (ADR-0051 Notes): a forecast that consumed a
series can name its `series_id` and the spec here says what that id measures,
which upstream produced it, and on what cadence.

Determinism: the registry is a literal dict (insertion-ordered, no set
iteration); `registered_series()` returns a sorted tuple so any consumer that
enumerates series does so in a stable order.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MetricPoint(BaseModel):
    """One scalar observation of a registered metric series.

    `ts` is UTC epoch seconds (matching the `metric_points.ts` column) — an
    integer, not a datetime, because the contract's only time operations are
    ordering comparisons and integer keys avoid tz round-trip ambiguity.
    Boundary-validated: a NaN/Inf value or negative timestamp never reaches
    the store.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str = Field(min_length=1)
    ts: int = Field(ge=0)
    value: float

    @field_validator("value")
    @classmethod
    def _value_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("metric value must be finite (no NaN/Inf)")
        return v


class MetricSeriesSpec(BaseModel):
    """What a registered series id measures: human description, producing
    source, and nominal cadence. The spec is documentation-grade metadata —
    the repository only checks *membership*; cadence is per-series and
    irregular by design (ADR-0051 Neutral)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)
    cadence: str = Field(min_length=1)


class UnknownMetricSeriesError(ValueError):
    """The series id is not in the registry — raised at the repository
    boundary before any read or write touches the table (ADR-0051)."""


SERIES_FNG_VALUE = "fng.value"
SERIES_COINGECKO_BTC_DOMINANCE = "coingecko.btc_dominance"
SERIES_COINGECKO_TOTAL_MCAP_USD = "coingecko.total_mcap_usd"
SERIES_BINANCE_FUNDING_RATE_BTCUSDT = "binance.funding_rate.BTCUSDT"
SERIES_BINANCE_FUNDING_RATE_ETHUSDT = "binance.funding_rate.ETHUSDT"
SERIES_BINANCE_OPEN_INTEREST_BTCUSDT = "binance.open_interest.BTCUSDT"
SERIES_BINANCE_OPEN_INTEREST_ETHUSDT = "binance.open_interest.ETHUSDT"
SERIES_COINMETRICS_BTC_MVRV = "coinmetrics.btc.mvrv"

# The accrual series have no free historical source (CoinGecko's historical
# /global is paid-only — Plan 0055 Decision), so they grow by write-through
# sampling: at most one point per hour, keyed to the snapshot timestamp
# truncated to the hour. Honest `None`s downstream until they warm up.
_ACCRUAL_CADENCE = (
    "accrual-only: hourly-truncated write-through on each successful macro "
    "fetch; no free history upstream"
)

# Binance USDⓈ-M perpetual funding prints (Plan 0056 / ADR-0052): full history
# since contract launch is backfillable by pagination; majors print every 8h,
# but cadence is taken from the data, never hardcoded outside display hints.
_BINANCE_FUNDING_CADENCE = (
    "8h prints for majors (cadence read from the data, not assumed); full "
    "history backfillable by pagination since contract launch"
)

# Binance USDⓈ-M open interest (Plan 0056 phase 3 / ADR-0052): upstream serves
# only the latest ~1 month (`openInterestHist`), so the series is *recorded* —
# seeded once from that window, then grown by hour-truncated snapshot accrual
# (first write in an hour wins). History beyond the seed window is gone forever.
_BINANCE_OPEN_INTEREST_CADENCE = (
    "hourly: one-time seed from the ~30-day openInterestHist window, then "
    "hour-truncated snapshot accrual (first write in an hour wins); no deeper "
    "history exists upstream"
)

# CoinMetrics community MVRV (Plan 0057 / ADR-0053): full daily history is
# backfillable keyless from `community-api.coinmetrics.io` back to 2011-12-29
# (phase-1 probe, 2026-06-14). Realized cap and SOPR turned out paywalled on the
# community tier, so MVRV is the one series this source produces (ADR-0053 probe
# outcome). `CapMVRVCur` is the published, versioned market-cap/realized-cap
# ratio — a cycle-valuation lens alongside Mayer/200W in `btc_cycle_snapshot`.
_COINMETRICS_MVRV_CADENCE = (
    "daily (CoinMetrics CapMVRVCur), full history back to 2011-12-29; "
    "backfillable keyless by pagination, then incremental"
)

# The one module-level registry (ADR-0051). Adding an entry here in source is
# how a plan registers a series — never at runtime. Plan 0055 phase 2 registers
# `fng.value`; phase 3 the two CoinGecko macro series. Plan 0056 phase 1
# registers the two Binance funding-rate series; phase 3 the two open-interest
# series. Plan 0057 registers `coinmetrics.btc.mvrv`.
SERIES_REGISTRY: dict[str, MetricSeriesSpec] = {
    SERIES_FNG_VALUE: MetricSeriesSpec(
        series_id=SERIES_FNG_VALUE,
        description="Crypto Fear & Greed index (0-100), daily since 2018-02-01",
        source="alternative.me-fng",
        cadence="daily; full history backfillable in one keyless call (?limit=0)",
    ),
    SERIES_COINGECKO_BTC_DOMINANCE: MetricSeriesSpec(
        series_id=SERIES_COINGECKO_BTC_DOMINANCE,
        description="BTC dominance, percent of total crypto market cap (0-100)",
        source="coingecko",
        cadence=_ACCRUAL_CADENCE,
    ),
    SERIES_COINGECKO_TOTAL_MCAP_USD: MetricSeriesSpec(
        series_id=SERIES_COINGECKO_TOTAL_MCAP_USD,
        description="Total crypto market capitalization in USD",
        source="coingecko",
        cadence=_ACCRUAL_CADENCE,
    ),
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT: MetricSeriesSpec(
        series_id=SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
        description=(
            "Binance USDS-M perpetual funding rate for BTCUSDT, decimal per "
            "funding interval (e.g. 0.0001 = 1bp); history since 2019-09"
        ),
        source="binance-futures",
        cadence=_BINANCE_FUNDING_CADENCE,
    ),
    SERIES_BINANCE_FUNDING_RATE_ETHUSDT: MetricSeriesSpec(
        series_id=SERIES_BINANCE_FUNDING_RATE_ETHUSDT,
        description=(
            "Binance USDS-M perpetual funding rate for ETHUSDT, decimal per "
            "funding interval (e.g. 0.0001 = 1bp); history since 2019-11"
        ),
        source="binance-futures",
        cadence=_BINANCE_FUNDING_CADENCE,
    ),
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT: MetricSeriesSpec(
        series_id=SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
        description=(
            "Binance USDS-M perpetual open interest for BTCUSDT, in base-asset "
            "units (BTC); recorded from deployment, seeded by the ~30-day "
            "upstream window"
        ),
        source="binance-futures",
        cadence=_BINANCE_OPEN_INTEREST_CADENCE,
    ),
    SERIES_BINANCE_OPEN_INTEREST_ETHUSDT: MetricSeriesSpec(
        series_id=SERIES_BINANCE_OPEN_INTEREST_ETHUSDT,
        description=(
            "Binance USDS-M perpetual open interest for ETHUSDT, in base-asset "
            "units (ETH); recorded from deployment, seeded by the ~30-day "
            "upstream window"
        ),
        source="binance-futures",
        cadence=_BINANCE_OPEN_INTEREST_CADENCE,
    ),
    SERIES_COINMETRICS_BTC_MVRV: MetricSeriesSpec(
        series_id=SERIES_COINMETRICS_BTC_MVRV,
        description=(
            "Bitcoin MVRV (market value / realized value), CoinMetrics "
            "CapMVRVCur; daily, full history since 2011-12-29"
        ),
        source="coinmetrics-community",
        cadence=_COINMETRICS_MVRV_CADENCE,
    ),
}


def is_registered(series_id: str) -> bool:
    """Whether `series_id` is in the registry."""
    return series_id in SERIES_REGISTRY


def get_series_spec(series_id: str) -> MetricSeriesSpec:
    """Return the spec for a registered series id, or raise
    `UnknownMetricSeriesError` — the loud-failure half of "the registry is
    the schema"."""
    spec = SERIES_REGISTRY.get(series_id)
    if spec is None:
        raise UnknownMetricSeriesError(
            f"unregistered metric series id {series_id!r} — registered: "
            f"{', '.join(registered_series()) or '(none)'}",
        )
    return spec


def registered_series() -> tuple[str, ...]:
    """All registered series ids, sorted for deterministic enumeration."""
    return tuple(sorted(SERIES_REGISTRY))


__all__ = [
    "SERIES_BINANCE_FUNDING_RATE_BTCUSDT",
    "SERIES_BINANCE_FUNDING_RATE_ETHUSDT",
    "SERIES_BINANCE_OPEN_INTEREST_BTCUSDT",
    "SERIES_BINANCE_OPEN_INTEREST_ETHUSDT",
    "SERIES_COINGECKO_BTC_DOMINANCE",
    "SERIES_COINGECKO_TOTAL_MCAP_USD",
    "SERIES_COINMETRICS_BTC_MVRV",
    "SERIES_FNG_VALUE",
    "SERIES_REGISTRY",
    "MetricPoint",
    "MetricSeriesSpec",
    "UnknownMetricSeriesError",
    "get_series_spec",
    "is_registered",
    "registered_series",
]
