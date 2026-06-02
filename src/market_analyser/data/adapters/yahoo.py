"""Yahoo Finance adapter — thin wrapper over the in-house OHLCV fetcher.

The adapter does three things the raw fetcher does not:
1. Validates the contract's `start: datetime, end: datetime` window and translates
   the canonical timeframe into Yahoo's native `interval` string. The window is
   passed through verbatim — the fetcher requests it via absolute period1/period2
   (Plan 0031), so no span→range mapping happens here.
2. Validates every bar through the `Bar` pydantic model — rejecting NaN, negative,
   non-UTC, or out-of-window data per `best-practices.md`'s boundary rule.
3. Filters the response to the requested `[start, end]` window.

Per ADR-0007, this adapter is package-internal: downstream code imports the
`MarketDataProvider` Protocol, not this class.
"""

from __future__ import annotations

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
from market_analyser.data.sources import OhlcvSource, SymbolSearchSource
from market_analyser.data.timeframes import require_native_interval, uses_intraday_timestamp
from market_analyser.data.types import Bar, SymbolInfo

# Yahoo's chart endpoint occasionally times out under load; the shared client's
# defaults (transient-failure handling, bounded concurrency) cover it. No
# in-memory result store: OHLCV bars are persisted cross-session in SQLite, so
# the shared client runs store-less here and only its request timeout is tuned.
_REQUEST_TIMEOUT_SECONDS = 15.0


class _FetchOhlcvFn(Protocol):
    def __call__(
        self, symbol: str, start: datetime, end: datetime, interval: str = ...
    ) -> list[dict[str, Any]]: ...


# Result cap for symbol search. Yahoo honours `quotesCount`, so this also bounds
# the upstream payload. Kept adapter-internal (not a Protocol/route parameter):
# the dropdown only ever shows a handful of suggestions and the plan's `limit`
# was an explicitly-tunable open question, not a contract (Plan 0024).
_DEFAULT_QUOTES_COUNT = 10


class YahooAdapter(OhlcvSource, SymbolSearchSource):
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
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[dict[str, Any]]:
        return _fetch_yahoo_ohlcv(symbol, start, end, interval, client=self._client)

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
        # Validates the timeframe and yields the Yahoo `interval` to request:
        # canonical "1w" → "1wk", "15m"/"1h"/"1d" unchanged. Derived timeframes
        # (e.g. resampled 4h) raise here — the provider fetches their base instead.
        interval = require_native_interval(timeframe)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")
        if start >= end:
            raise ValueError(f"start ({start}) must be strictly before end ({end})")

        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)

        # The window is fetched verbatim via absolute period1/period2 (Plan 0031),
        # so there is no span→range cap here. Intraday-history horizons are real
        # Yahoo limits, enforced by `default_provider._exceeds_history_cap` before
        # this adapter is reached; uncapped timeframes (1d, 1w) have no ceiling.
        try:
            raw = self._fetch(symbol, start_utc, end_utc, interval)
        except ResilientHttpError as err:
            raise _classify_http_error(err, symbol) from err

        if not raw:
            # An empty response for a structurally-valid symbol over an absolute
            # window is treated as unknown-symbol: zero bars on a known-good
            # interval is implausible for a live, listed name (Plan 0013 phase 1).
            # A non-empty response whose rows all fall outside the exact requested
            # window still returns `[]` below (the gap-too-small case the caller's
            # `if not fetched` path handles) — only a genuinely empty upstream
            # response lands here.
            raise UnknownSymbolError(
                f"yahoo: no rows for {symbol.upper()!r} over "
                f"[{start_utc.isoformat()}, {end_utc.isoformat()}] ({timeframe}) — "
                f"symbol is likely unknown or unlisted",
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


def _parse_event_ts(date_str: str, timeframe: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M" if uses_intraday_timestamp(timeframe) else "%Y-%m-%d"
    return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
