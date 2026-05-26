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

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.adapters._yahoo_fetch import _fetch_yahoo_ohlcv
from market_analyser.data.adapters._yahoo_search import _fetch_yahoo_search
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.types import Bar, SymbolInfo

_logger = logging.getLogger(__name__)

# Yahoo's chart endpoint occasionally times out under load; the shared client's
# defaults (transient-failure handling, bounded concurrency) cover it. No
# in-memory result store: OHLCV bars are persisted cross-session in SQLite, so
# the shared client runs store-less here and only its request timeout is tuned.
_REQUEST_TIMEOUT_SECONDS = 15.0


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

# Result cap for symbol search. Yahoo honours `quotesCount`, so this also bounds
# the upstream payload. Kept adapter-internal (not a Protocol/route parameter):
# the dropdown only ever shows a handful of suggestions and the plan's `limit`
# was an explicitly-tunable open question, not a contract (Plan 0024).
_DEFAULT_QUOTES_COUNT = 10


class YahooAdapter:
    """Adapter over the in-house Yahoo Chart fetcher. Returns validated Bars."""

    def __init__(
        self,
        fetcher: _FetchOhlcvFn | None = None,
        http_client: ResilientHttpClient | None = None,
    ) -> None:
        self._client = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="yahoo",
                request_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
            )
        )
        self._fetch: _FetchOhlcvFn = fetcher if fetcher is not None else self._default_fetch

    def _default_fetch(
        self,
        symbol: str,
        period: str,
        interval: str = "1d",
    ) -> list[dict[str, Any]]:
        return _fetch_yahoo_ohlcv(symbol, period, interval, client=self._client)

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
        try:
            raw = self._fetch(symbol, period, timeframe)
        except ResilientHttpError as err:
            raise _classify_http_error(err, symbol) from err

        if not raw:
            # An empty response on a >= 1mo period for a structurally-valid
            # symbol is treated as unknown-symbol: a multi-day window of zero
            # bars on a known-good interval is implausible for a live, listed
            # name (Plan 0013 phase 1). `_smallest_period_for` floors the period
            # at "1mo" (31d), so the period_days >= 30 heuristic always holds
            # here. A non-empty response whose rows all fall outside the exact
            # requested window still returns `[]` below (the gap-too-small case
            # the caller's `if not fetched` path handles) — only a genuinely
            # empty upstream response lands here.
            raise UnknownSymbolError(
                f"yahoo: no rows for {symbol.upper()!r} over period {period!r} "
                f"({timeframe}) — symbol is likely unknown or unlisted",
                symbol=symbol.upper(),
            )

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

    def search(self, query: str) -> list[SymbolInfo]:
        """Resolve a free-text ``query`` to Yahoo-native symbols via the
        ``/v1/finance/search`` endpoint, returning validated `SymbolInfo`
        results in Yahoo's upstream relevance order (ADR-0026).

        An empty/whitespace query short-circuits to ``[]`` with no network call.
        A zero-match search also returns ``[]`` — that is not an error and is
        deliberately distinct from `UnknownSymbolError` (which the OHLCV fetch
        raises). Quotes without a usable ``symbol`` are skipped; a quote missing
        its name falls back to the symbol.
        """
        query = query.strip()
        if not query:
            return []
        try:
            raw = _fetch_yahoo_search(
                query,
                client=self._client,
                quotes_count=_DEFAULT_QUOTES_COUNT,
            )
        except ResilientHttpError as err:
            raise _classify_search_error(err) from err
        results: list[SymbolInfo] = []
        for quote in raw:
            info = _quote_to_symbol_info(quote)
            if info is not None:
                results.append(info)
        return results


def _classify_search_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent symbol-search `ResilientHttpError` into
    the typed taxonomy, mirroring `_classify_http_error` for the chart endpoint.
    Search has no unknown-symbol path (a zero-match query returns `[]` upstream
    of any error), so only rate-limit vs upstream-unavailable apply."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            "yahoo: rate limited (HTTP 429) on symbol search",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(f"yahoo: upstream unavailable ({detail}) on symbol search")


def _quote_to_symbol_info(quote: dict[str, Any]) -> SymbolInfo | None:
    """Map one Yahoo search quote onto `SymbolInfo`, or `None` if it carries no
    usable symbol. Field precedence matches the Plan 0024 done-when:
    name←longname/shortname/symbol, exchange←exchDisp/exchange, quote_type←
    typeDisp/quoteType (first present in each group)."""
    symbol = str(quote.get("symbol") or "").strip()
    if not symbol:
        return None  # SymbolInfo requires a non-empty symbol; skip the unidentifiable
    return SymbolInfo(
        symbol=symbol,
        name=_first_present(quote, ("longname", "shortname")) or symbol,
        exchange=_first_present(quote, ("exchDisp", "exchange")),
        quote_type=_first_present(quote, ("typeDisp", "quoteType")),
    )


def _first_present(quote: dict[str, Any], keys: tuple[str, ...]) -> str:
    """First truthy value among ``keys`` in ``quote``, stringified, else ``""``."""
    for key in keys:
        value = quote.get(key)
        if value:
            return str(value)
    return ""


def _classify_http_error(err: ResilientHttpError, symbol: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy (Plan 0013 phase 1), mirroring the seam StockTwits uses for its
    404. HTTP 429 → rate-limited (carrying the upstream `Retry-After`); any
    other non-429 status or transport-level failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"yahoo: rate limited (HTTP 429) fetching {symbol.upper()!r}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"yahoo: upstream unavailable ({detail}) fetching {symbol.upper()!r}",
    )


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (urllib preserves the upstream's casing)."""
    lowered = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lowered), None)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a `Retry-After` header as whole seconds. The HTTP-date form is not
    supported (returns None) — the agent gets the rate-limit signal either way."""
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _smallest_period_for(span_days: int) -> str:
    for label, days in _PERIOD_DAYS:
        if days >= span_days:
            return label
    return _PERIOD_DAYS[-1][0]


def _parse_event_ts(date_str: str, timeframe: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M" if timeframe == "1h" else "%Y-%m-%d"
    return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
