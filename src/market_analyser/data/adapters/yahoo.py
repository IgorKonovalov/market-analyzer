"""Yahoo Finance adapter — thin wrapper over the in-house OHLCV fetcher.

The adapter does three things the raw fetcher does not:
1. Translates the contract's `start: datetime, end: datetime` window into the
   range-period strings Yahoo's chart API accepts.
2. Validates every bar through the `Bar` pydantic model — rejecting NaN, negative,
   non-UTC, or out-of-window data per `best-practices.md`'s boundary rule.
3. Filters the response to the requested `[start, end]` window.

Per ADR-0007, this adapter is package-internal: downstream code imports the
`MarketDataProvider` Protocol, not this class.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from market_analyser.data.adapters._yahoo_fetch import _fetch_yahoo_ohlcv
from market_analyser.data.types import Bar

_logger = logging.getLogger(__name__)


class _FetchOhlcvFn(Protocol):
    def __call__(self, symbol: str, period: str, interval: str = ...) -> list[dict[str, Any]]: ...


# Range strings supported by Yahoo's chart API via the in-house fetcher.
# Ordered shortest → longest so we can pick the smallest sufficient period.
_PERIOD_DAYS: tuple[tuple[str, int], ...] = (
    ("1mo", 31),
    ("3mo", 93),
    ("6mo", 186),
    ("1y", 366),
    ("2y", 732),
)
_MAX_PERIOD_DAYS = _PERIOD_DAYS[-1][1]

_VALID_TIMEFRAMES: frozenset[str] = frozenset({"1d", "1h"})


class YahooAdapter:
    """Adapter over the in-house Yahoo Chart fetcher. Returns validated Bars."""

    def __init__(self, fetcher: _FetchOhlcvFn | None = None) -> None:
        self._fetch = fetcher if fetcher is not None else _fetch_yahoo_ohlcv

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if timeframe not in _VALID_TIMEFRAMES:
            raise ValueError(
                f"timeframe {timeframe!r} not supported (supported: {sorted(_VALID_TIMEFRAMES)})",
            )
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")
        if start >= end:
            raise ValueError(f"start ({start}) must be strictly before end ({end})")

        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)

        span_days = (end_utc - start_utc).days + 1
        if span_days > _MAX_PERIOD_DAYS:
            raise ValueError(
                f"requested span {span_days}d exceeds supported max {_MAX_PERIOD_DAYS}d",
            )

        period = _smallest_period_for(span_days)
        period_days = next((d for label, d in _PERIOD_DAYS if label == period), span_days)
        if period_days > span_days:
            _logger.debug(
                "yahoo over-fetch: requested span=%dd, picked period=%s (%dd), ratio=%.2fx",
                span_days,
                period,
                period_days,
                period_days / max(span_days, 1),
            )
        raw = self._fetch(symbol, period, timeframe)

        bars: list[Bar] = []
        for row in raw:
            ts = _parse_event_ts(row["date"], timeframe)
            if ts < start_utc or ts > end_utc:
                continue
            bars.append(
                Bar(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    event_ts=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    source="yahoo",
                ),
            )
        return bars


def _smallest_period_for(span_days: int) -> str:
    for label, days in _PERIOD_DAYS:
        if days >= span_days:
            return label
    return _PERIOD_DAYS[-1][0]


def _parse_event_ts(date_str: str, timeframe: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M" if timeframe == "1h" else "%Y-%m-%d"
    return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
