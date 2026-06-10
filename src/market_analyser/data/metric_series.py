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

# The accrual series have no free historical source (CoinGecko's historical
# /global is paid-only — Plan 0055 Decision), so they grow by write-through
# sampling: at most one point per hour, keyed to the snapshot timestamp
# truncated to the hour. Honest `None`s downstream until they warm up.
_ACCRUAL_CADENCE = (
    "accrual-only: hourly-truncated write-through on each successful macro "
    "fetch; no free history upstream"
)

# The one module-level registry (ADR-0051). Adding an entry here in source is
# how a plan registers a series — never at runtime. Plan 0055 phase 2 registers
# `fng.value`; phase 3 the two CoinGecko macro series.
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
    "SERIES_COINGECKO_BTC_DOMINANCE",
    "SERIES_COINGECKO_TOTAL_MCAP_USD",
    "SERIES_FNG_VALUE",
    "SERIES_REGISTRY",
    "MetricPoint",
    "MetricSeriesSpec",
    "UnknownMetricSeriesError",
    "get_series_spec",
    "is_registered",
    "registered_series",
]
