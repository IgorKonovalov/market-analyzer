"""Canonical timeframe registry (Plan 0025 / ADR-0028).

The single source of truth for everything the data layer needs to know about a
timeframe: its **bar duration** (for coverage/gap math), its **Yahoo fetch
interval** (`None` when the timeframe is derived rather than fetched), its
**resampled-from base** (`None` when native), and its **max-history cap**
(`None` when effectively unbounded). The Yahoo adapter's valid-set and interval
selection, the coverage/gap math, the resampler, and the boundary tool
descriptions all read this registry instead of carrying their own literals.

`SUPPORTED_TIMEFRAMES` stays the canonical *set* in `annotations/types.py` (so
the MCP and annotations validators keep their existing import with no new
`annotations → data` dependency); `tests/data/test_timeframes.py` asserts the
registry keys equal that frozenset, so the two views cannot drift (ADR-0028).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class TimeframeSpec:
    """Everything the data layer needs to know about one canonical timeframe.

    `yahoo_interval` is `None` exactly when the timeframe is derived in-house
    (`resampled_from` set); `resampled_from` is `None` exactly when the timeframe
    is fetched natively (`yahoo_interval` set). `max_history` is `None` for
    timeframes whose history is effectively unbounded (daily, weekly)."""

    canonical: str
    bar_duration: timedelta
    yahoo_interval: str | None
    resampled_from: str | None
    max_history: timedelta | None


# Insertion order is cadence-ascending so `supported_timeframes_label()` reads
# naturally (15m, 1h, …). Yahoo serves 15m/1h/1d/1wk natively but has no 4h
# interval, so 4h is added (Plan 0025 phase 2) as a 1h-derived resample.
_REGISTRY: dict[str, TimeframeSpec] = {
    "15m": TimeframeSpec("15m", timedelta(minutes=15), "15m", None, timedelta(days=60)),
    "1h": TimeframeSpec("1h", timedelta(hours=1), "1h", None, timedelta(days=730)),
    # 4h is derived in-house from native 1h bars (Yahoo has no 4h interval); it
    # inherits the 1h base's history reach. yahoo_interval is None so it is never
    # fetched directly — the provider resamples it on read (Plan 0025 ph2 / ADR-0028).
    "4h": TimeframeSpec("4h", timedelta(hours=4), None, "1h", timedelta(days=730)),
    "1d": TimeframeSpec("1d", _DAY, "1d", None, None),
    "1w": TimeframeSpec("1w", timedelta(days=7), "1wk", None, None),
}

# Yahoo intervals whose bars carry an intraday timestamp (`%Y-%m-%d %H:%M`); the
# rest use a date-only timestamp. Derived from the registry so adding a timeframe
# updates the parser's format decision in one place.
_INTRADAY_YAHOO_INTERVALS: frozenset[str] = frozenset(
    spec.yahoo_interval
    for spec in _REGISTRY.values()
    if spec.yahoo_interval is not None and spec.bar_duration < _DAY
)


def timeframe_spec(tf: str) -> TimeframeSpec:
    """The `TimeframeSpec` for `tf`, or `ValueError` if `tf` is not registered.

    A bad timeframe is a caller bug, not an upstream failure, so it raises
    `ValueError` (consistent with the adapter's input-boundary contract)."""
    try:
        return _REGISTRY[tf]
    except KeyError:
        raise ValueError(
            f"unknown timeframe {tf!r} (known: {sorted(_REGISTRY)})",
        ) from None


def bar_duration(tf: str) -> timedelta:
    """The wall-clock span of one bar at timeframe `tf` (for coverage/gap math)."""
    return timeframe_spec(tf).bar_duration


def yahoo_interval(tf: str) -> str | None:
    """The Yahoo chart `interval` for `tf`, or `None` when `tf` is derived."""
    return timeframe_spec(tf).yahoo_interval


def resampled_from(tf: str) -> str | None:
    """The base timeframe `tf` is resampled from, or `None` when `tf` is native."""
    return timeframe_spec(tf).resampled_from


def max_history(tf: str) -> timedelta | None:
    """The furthest back `tf` bars are available, or `None` when unbounded."""
    return timeframe_spec(tf).max_history


def registry_timeframes() -> frozenset[str]:
    """Every registered canonical timeframe — must equal `SUPPORTED_TIMEFRAMES`."""
    return frozenset(_REGISTRY)


def native_timeframes() -> frozenset[str]:
    """Timeframes fetched natively from Yahoo (those with a `yahoo_interval`).
    Derived timeframes (e.g. resampled 4h) are excluded: the provider intercepts
    them before they reach the adapter, so `YahooAdapter.fetch_ohlcv` must reject
    them as not-natively-fetchable."""
    return frozenset(tf for tf, spec in _REGISTRY.items() if spec.yahoo_interval is not None)


def require_native_interval(tf: str) -> str:
    """Validate that `tf` is a natively-fetchable timeframe and return its Yahoo
    interval. Raises `ValueError` if `tf` is unknown or is derived (resampled) —
    the single check the Yahoo adapter uses in place of a hard-coded valid-set."""
    spec = timeframe_spec(tf)
    if spec.yahoo_interval is None:
        raise ValueError(
            f"timeframe {tf!r} is derived (resampled from {spec.resampled_from!r}), "
            "not natively fetchable from Yahoo",
        )
    return spec.yahoo_interval


def uses_intraday_timestamp(tf: str) -> bool:
    """Whether `tf` bars carry an intraday (`%Y-%m-%d %H:%M`) timestamp rather than
    a date-only one — true for sub-daily cadences (15m, 1h, …)."""
    return timeframe_spec(tf).bar_duration < _DAY


def yahoo_interval_uses_intraday_timestamp(interval: str) -> bool:
    """Whether bars fetched at Yahoo `interval` carry an intraday timestamp. Keyed
    on the Yahoo interval (not the canonical timeframe) because the fetcher parses
    the chart payload with the interval it requested."""
    return interval in _INTRADAY_YAHOO_INTERVALS


def supported_timeframes_label() -> str:
    """Comma-separated, cadence-ordered list of supported timeframes for the MCP
    tool descriptions (so the agent-facing docs never carry a hand-maintained
    literal that can drift from the registry)."""
    return ", ".join(sorted(_REGISTRY, key=lambda tf: _REGISTRY[tf].bar_duration))


__all__ = [
    "TimeframeSpec",
    "bar_duration",
    "max_history",
    "native_timeframes",
    "registry_timeframes",
    "require_native_interval",
    "resampled_from",
    "supported_timeframes_label",
    "timeframe_spec",
    "uses_intraday_timestamp",
    "yahoo_interval",
    "yahoo_interval_uses_intraday_timestamp",
]
