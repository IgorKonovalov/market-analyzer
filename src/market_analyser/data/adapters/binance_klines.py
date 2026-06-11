"""Binance spot klines adapter — Plan 0058 phase 1 (ADR-0052, ADR-0031, ADR-0019).

Keyless OHLCV from `api.binance.com` (`GET /api/v3/klines`) through
`BinanceSpotHttpClient`, a `ResilientHttpClient` subclass whose classifier pins
the one quirk that matters here: **HTTP 451 is the geo-restriction response**
(Binance returns it from restricted locations even for public read-only
endpoints) and is `PERMANENT` — never retried, surfaced as the typed
`GeoRestrictedError` so the fallback decision is made as an ADR-0052 follow-up
by a human, never improvised in the adapter (same contract as the Plan 0056
derivatives adapter, `binance_derivatives.py`).

`fetch_ohlcv` implements `OhlcvSource` (ADR-0031) with absolute-window
semantics from day one — the Plan 0031 lesson applied: the requested
`[start, end]` goes to the wire verbatim as `startTime`/`endTime` epoch
milliseconds, so a past-ending historical window returns exactly that window's
bars, never a now-relative approximation. Pagination walks 1000-bar pages
(the documented `/api/v3/klines` max) by advancing a `startTime` cursor past
each page's last open time; the cursor floor is a **nonzero** epoch-millisecond
(Binance treats a falsy `startTime` as "parameter not sent" — the live-verified
Plan 0056 phase-2 smoke finding, assumed shared by the spot API). **An empty
page is end-of-history, not an error** — full-history-by-pagination is
confirmed in practice but not doc-guaranteed (ADR-0052 Notes), so the
terminator is the upstream running out of rows.

Each kline array maps onto the existing `Bar` (open time as the bar timestamp;
OHLCV decimal strings parsed at the wire boundary). A zero or negative price is
upstream garbage and raises the typed `BinanceKlinesError` — never silently
passed, never silently dropped. An empty *overall* response is classified by
window recency per ADR-0033 (identically to the Yahoo adapter): a window
reaching the leading edge must have data for a live symbol, so emptiness there
is `UnknownSymbolError`; a strictly-historical empty window is a legitimate
end-of-history and returns `[]`.

The interval map covers every canonical-registry timeframe natively — Binance
serves `4h` directly, so Binance symbols never take the ADR-0028 resample path
(the provider's routing applies that per source in Plan 0058 phase 2).

A `ResilientHttpError` (exhausted retries / permanent failure) is translated
into the typed `UpstreamDataError` taxonomy (451 → geo-restricted, 429 →
rate-limited, else unavailable). A shape-broken 2xx payload raises
`BinanceKlinesError`.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol / composition root, never by importing this class.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import (
    ErrorKind,
    HttpResponse,
    ResilientHttpClient,
    ResilientHttpError,
)
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.timeframes import bar_duration, timeframe_spec
from market_analyser.data.types import Bar

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_SOURCE = "binance"

# Upstream page cap for /api/v3/klines (ADR-0052 verified facts: max 1000
# bars/call, weight 2 at 6,000/min — effectively unconstrained here).
_PAGE_LIMIT = 1000

# Canonical timeframe → Binance spot interval. Every registry timeframe is
# native on Binance (incl. 4h — no resample path for Binance symbols, ADR-0052);
# only the two multi-letter suffixes differ from the canonical spelling.
# `tests/data/test_binance_klines_adapter.py` pins this map's keys against the
# registry so a new canonical timeframe cannot silently miss a mapping.
_BINANCE_INTERVALS: dict[str, str] = {
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
    "1mo": "1M",
}

# First-page `startTime` floor: 1 ms after the epoch — at/before any listing,
# and never zero. Binance treats `startTime=0` exactly like an absent parameter
# and serves only the most recent window (verified live 2026-06-10 against the
# futures API, plan 0056 phase 2 smoke finding; conservatively assumed shared
# by the spot API), so a falsy cursor never reaches the wire.
_HISTORY_START_MS = 1

# A kline array's leading fields, per the official /api/v3/klines docs:
# [0] open time (ms), [1] open, [2] high, [3] low, [4] close, [5] volume —
# the rest (close time, quote volume, trades, taker volumes) are unused here.
_KLINE_MIN_FIELDS = 6


class BinanceKlinesError(ValueError):
    """The upstream 2xx payload broke shape (non-list body, malformed kline
    array, non-numeric field, a zero/negative price, or a non-advancing page
    cursor) — raised at the adapter boundary before anything reaches the cache.
    Upstream drift surfaces typed, never as a silently-skipped bar."""


class BinanceSpotHttpClient(ResilientHttpClient):
    """`ResilientHttpClient` that pins Binance's geo-restriction response.

    HTTP 451 means the caller's network is geo-blocked (ADR-0052) — a
    structural condition, not a transient fault. The base classifier already
    treats non-429 4xx as `PERMANENT`; the explicit branch makes the
    never-retry guarantee independent of the base policy (the same pin as
    `BinanceFuturesHttpClient` in the Plan 0056 derivatives adapter).
    """

    def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
        if response is not None and response.status_code == 451:
            return ErrorKind.PERMANENT
        return super().classify(exc, response)


class BinanceKlinesAdapter:
    """Fetches Binance spot OHLCV bars (`OhlcvSource`, ADR-0031)."""

    def __init__(self, http_client: ResilientHttpClient | None = None) -> None:
        self._http = (
            http_client
            if http_client is not None
            else BinanceSpotHttpClient(
                source_name=_SOURCE,
                # History pages are one-shot reads persisted in the SQLite bars
                # cache; an in-memory TTL cache buys nothing.
                cache_ttl_seconds=0.0,
            )
        )

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        now: datetime | None = None,
    ) -> list[Bar]:
        """Raw klines for the absolute `[start, end]` window, paginated at 1000
        bars/page, deduplicated by open time and returned in ascending
        timestamp order. Empty page = end-of-history (ADR-0052); an empty
        overall response is classified by window recency (ADR-0033 — see the
        module docstring).

        Raises `GeoRestrictedError` on HTTP 451 (never retried),
        `RateLimitedError` on 429, `UpstreamUnavailableError` on other upstream
        exhaustion, `UnknownSymbolError` on a leading-edge empty window,
        `BinanceKlinesError` on a shape-broken payload, and `ValueError` for
        caller bugs (bad timeframe, naive datetimes, inverted window)."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        interval = _binance_interval(timeframe)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")
        if start >= end:
            raise ValueError(f"start ({start}) must be strictly before end ({end})")

        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        end_ms = int(end_utc.timestamp() * 1000)
        # Clamp to the nonzero floor: Binance discards a falsy startTime and
        # would silently serve the latest window instead of the requested one.
        cursor_ms = max(int(start_utc.timestamp() * 1000), _HISTORY_START_MS)

        bars_by_ts: dict[int, Bar] = {}
        while True:
            params: dict[str, str | int | float] = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor_ms,
                "endTime": end_ms,
                "limit": _PAGE_LIMIT,
            }
            try:
                payload = self._http.get(_KLINES_URL, params=params, expect_json=True).json()
            except ResilientHttpError as err:
                raise _classify_error(err, symbol) from err
            page = _parse_page(payload, symbol=symbol, timeframe=timeframe)
            if not page:
                break  # empty page = end-of-history, not an error (ADR-0052)
            for ts_ms, bar in page:
                stored = bars_by_ts.get(ts_ms)
                if stored is not None and stored != bar:
                    raise BinanceKlinesError(
                        f"binance: two klines for {symbol} ({timeframe}) at open time "
                        f"{ts_ms} with different values",
                    )
                bars_by_ts[ts_ms] = bar
            last_ms = page[-1][0]
            if last_ms < cursor_ms:
                raise BinanceKlinesError(
                    f"binance: klines page for {symbol} did not advance past "
                    f"startTime={cursor_ms} (last open time={last_ms}) — refusing to loop",
                )
            cursor_ms = last_ms + 1

        bars = [
            bar for _, bar in sorted(bars_by_ts.items()) if start_utc <= bar.event_ts <= end_utc
        ]
        if not bars:
            # ADR-0033: classify emptiness by window recency, mirroring the
            # Yahoo adapter — at the leading edge a live, listed pair must have
            # data, so an empty answer there means the symbol is unknown; a
            # strictly-historical empty window is a legitimate end-of-history
            # (the pair listed after the window) and returns []. `now` comes
            # from the provider's `_now`/`as_of` seam; absent it, the
            # conservative leading-edge reading applies.
            if _reaches_leading_edge(end_utc, timeframe, now):
                raise UnknownSymbolError(
                    f"binance: no klines for {symbol!r} over "
                    f"[{start_utc.isoformat()}, {end_utc.isoformat()}] ({timeframe}) — "
                    f"symbol is likely unknown or unlisted",
                    symbol=symbol,
                )
            return []
        return bars


def _binance_interval(timeframe: str) -> str:
    """The Binance spot `interval` for a canonical timeframe. Validates against
    the canonical registry first (a bad timeframe is a caller bug →
    `ValueError`, consistent with the adapter input-boundary contract)."""
    timeframe_spec(timeframe)  # raises ValueError for an unregistered timeframe
    return _BINANCE_INTERVALS[timeframe]


def _reaches_leading_edge(end: datetime, timeframe: str, now: datetime | None) -> bool:
    """Whether the requested window's `end` reaches the leading edge — within
    one bar of `now` (ADR-0033). Identical reading to the Yahoo adapter's."""
    if now is None:
        return True
    return end >= now - bar_duration(timeframe)


def _parse_page(
    payload: Any,
    *,
    symbol: str,
    timeframe: str,
) -> list[tuple[int, Bar]]:
    """Parse one `/api/v3/klines` page into `(open_time_ms, bar)` pairs,
    preserving upstream order (the cursor advances past the page's last open
    time, so order matters). Shape drift and zero/negative prices raise
    `BinanceKlinesError`."""
    if not isinstance(payload, list):
        raise BinanceKlinesError(
            f"binance: klines payload for {symbol} ({timeframe}) is not a list",
        )
    page: list[tuple[int, Bar]] = []
    for entry in payload:
        if not isinstance(entry, list) or len(entry) < _KLINE_MIN_FIELDS:
            raise BinanceKlinesError(
                f"binance: malformed kline array for {symbol} ({timeframe})",
            )
        open_time = entry[0]
        if isinstance(open_time, bool) or not isinstance(open_time, int):
            raise BinanceKlinesError(
                f"binance: kline for {symbol} ({timeframe}) missing integer open time",
            )
        open_ = _price(entry[1], field="open", symbol=symbol)
        high = _price(entry[2], field="high", symbol=symbol)
        low = _price(entry[3], field="low", symbol=symbol)
        close = _price(entry[4], field="close", symbol=symbol)
        volume = _decimal_str(entry[5], field="volume", symbol=symbol)
        if volume < 0:
            raise BinanceKlinesError(
                f"binance: negative volume {volume!r} for {symbol} ({timeframe})",
            )
        page.append(
            (
                open_time,
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_ts=datetime.fromtimestamp(open_time / 1000, tz=UTC),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    source=_SOURCE,
                ),
            ),
        )
    return page


def _price(value: Any, *, field: str, symbol: str) -> float:
    """Parse a string-encoded price and enforce the Plan 0058 phase-1 rule:
    a zero or negative price is upstream garbage, raised typed — never a bar."""
    parsed = _decimal_str(value, field=field, symbol=symbol)
    if parsed <= 0:
        raise BinanceKlinesError(
            f"binance: non-positive {field} price {parsed!r} for {symbol}",
        )
    return parsed


def _decimal_str(value: Any, *, field: str, symbol: str) -> float:
    """Parse an upstream string-encoded decimal (the klines wire encoding for
    prices and volume) to `float`. Anything else is shape drift."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise BinanceKlinesError(
        f"binance: kline for {symbol} missing decimal-string {field!r}",
    )


def _classify_error(err: ResilientHttpError, symbol: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy. HTTP 451 → geo-restricted (ADR-0052: surfaced, never improvised
    around); 429 → rate-limited (carrying `Retry-After`); any other status or
    transport failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 451:
        return GeoRestrictedError(
            f"binance: geo-restricted (HTTP 451) fetching klines for {symbol} — "
            f"api.binance.com is blocked from this network (ADR-0052)",
        )
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"binance: rate limited (HTTP 429) fetching klines for {symbol}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"binance: upstream unavailable ({detail}) fetching klines for {symbol}",
    )


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (urllib preserves the upstream's casing)."""
    lowered = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lowered), None)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a `Retry-After` header as whole seconds; the HTTP-date form is
    unsupported (returns None) — the agent gets the rate-limit signal regardless."""
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


__all__ = [
    "BinanceKlinesAdapter",
    "BinanceKlinesError",
    "BinanceSpotHttpClient",
]
