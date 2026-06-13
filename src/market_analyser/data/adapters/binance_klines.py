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

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

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
from market_analyser.data.types import Bar, Quote, SymbolInfo

_logger = logging.getLogger(__name__)

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_TICKER_24HR_URL = "https://api.binance.com/api/v3/ticker/24hr"
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

# Result cap for symbol search, mirroring the Yahoo adapter's quotes count: the
# picker only ever shows a handful of suggestions (Plan 0024 precedent).
_MAX_SEARCH_RESULTS = 10

# The source label search results carry (`SymbolInfo.exchange`) so the picker
# can tell `BTCUSDT` (Binance) from `BTC-USD` (Yahoo's synthetic composite) —
# the two are different series and are never presented as interchangeable
# (ADR-0052 / Plan 0058 phase 3).
_SEARCH_EXCHANGE_LABEL = "Binance"
_SEARCH_QUOTE_TYPE = "Cryptocurrency"


class ExchangeSymbolSet(BaseModel):
    """The cached `exchangeInfo` symbol universe driving provider dispatch
    (Plan 0058 phase 2 / ADR-0052: a symbol routes to Binance iff it is in this
    set — no prefixes, no format heuristics). `fetched_at` is the upstream's
    own `serverTime` (never a local wall-clock read), so staleness is visible
    and refresh stays explicit."""

    model_config = ConfigDict(frozen=True)

    source: Literal["binance"]
    symbols: frozenset[str]
    fetched_at: datetime


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
    """Fetches Binance spot OHLCV bars (`OhlcvSource`, ADR-0031), resolves the
    symbol search (`SymbolSearchSource`), serves live quotes (`QuoteSource`,
    the Plan 0058 follow-up — see `get_quote`), and owns the cached
    `exchangeInfo` symbol set that drives provider dispatch (ADR-0052
    membership routing). The capability breadth is deliberate: this class
    already owns the spot HTTP client and the membership set, so a separate
    per-capability adapter would only duplicate both.

    `symbol_cache_path` is where the symbol set persists across sessions
    (wired by the composition root — `<data-dir>/binance_exchange_info.json`);
    `None` keeps the set process-memoized only. **Stale-but-present beats
    absent**: a cached set is used as-is with no TTL and no auto-refresh —
    only `refresh_symbols()` (or a missing cache) reaches the network, so a
    newly-listed pair misroutes to Yahoo (loud 404) until an explicit refresh,
    the accepted Plan 0058 trade."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        symbol_cache_path: Path | None = None,
    ) -> None:
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
        self._symbol_cache_path = symbol_cache_path
        self._symbols: frozenset[str] | None = None

    def is_known_symbol(self, symbol: str) -> bool:
        """Whether `symbol` is in the cached Binance symbol universe — the
        ADR-0052 membership test the provider routes OHLCV by."""
        return symbol.strip().upper() in self.known_symbols()

    def known_symbols(self) -> frozenset[str]:
        """The cached `exchangeInfo` symbol set: process memo, else the cache
        file (used as-is however stale — stale-but-present beats absent), else
        one lazy fetch-and-persist. If that lazy fetch fails, the failure is
        memoized as an **empty set for this process** (a warning is logged):
        routing degrades to Yahoo-for-everything — loud 404s for
        Binance-only symbols, the same failure shape as the plan's accepted
        stale-set misroute — rather than failing every non-Binance request or
        re-probing a dead upstream per call. `refresh_symbols()` (or a process
        restart) recovers."""
        if self._symbols is not None:
            return self._symbols
        cached = self._load_cached_symbols()
        if cached is not None:
            self._symbols = cached
            return cached
        try:
            return self.refresh_symbols()
        except UpstreamDataError as err:
            _logger.warning(
                "binance: exchangeInfo unavailable (%s) — no symbols will route to "
                "Binance until refresh_symbols() succeeds or the process restarts",
                type(err).__name__,
            )
            self._symbols = frozenset()
            return self._symbols

    def refresh_symbols(self) -> frozenset[str]:
        """Explicitly re-fetch `GET /api/v3/exchangeInfo`, update the process
        memo, and persist the set to the cache file (when one is wired). The
        one refresh path of the Plan 0058 risk note. Raises the same typed
        taxonomy as `fetch_ohlcv` (451 → `GeoRestrictedError`, never retried)
        and `BinanceKlinesError` on a shape-broken payload."""
        try:
            payload = self._http.get(_EXCHANGE_INFO_URL, expect_json=True).json()
        except ResilientHttpError as err:
            raise _classify_error(err, what="the exchangeInfo symbol set") from err
        symbol_set = _parse_exchange_info(payload)
        self._symbols = symbol_set.symbols
        self._persist_symbols(symbol_set)
        return symbol_set.symbols

    def search(self, query: str) -> list[SymbolInfo]:
        """`SymbolSearchSource` (ADR-0031) over the cached exchangeInfo set
        (Plan 0058 phase 3): case-insensitive substring match against the pair
        names, exact match first, then prefix matches, then the rest — each
        group alphabetical, capped at `_MAX_SEARCH_RESULTS` so the
        thousands-strong universe never floods the picker. Results carry the
        Binance source label (`exchange`) so the picker never presents
        `BTCUSDT` and `BTC-USD` as interchangeable (ADR-0052). Every hit is
        fetchable by `get_ohlcv` by construction — it is in the same membership
        set the provider routes by (the ADR-0026 chartable invariant).

        An empty/whitespace query short-circuits to `[]`; a zero-match query
        also returns `[]` (not an error). Deterministic: pure sorts over the
        set, no set-iteration order leaks into the result."""
        query = query.strip().upper()
        if not query:
            return []
        matches = sorted(s for s in self.known_symbols() if query in s)
        exact = [s for s in matches if s == query]
        prefixed = [s for s in matches if s != query and s.startswith(query)]
        rest = [s for s in matches if s != query and not s.startswith(query)]
        ranked = (exact + prefixed + rest)[:_MAX_SEARCH_RESULTS]
        return [
            SymbolInfo(
                symbol=s,
                name=s,
                exchange=_SEARCH_EXCHANGE_LABEL,
                quote_type=_SEARCH_QUOTE_TYPE,
            )
            for s in ranked
        ]

    def _load_cached_symbols(self) -> frozenset[str] | None:
        """The persisted symbol set, or `None` when there is no cache file. A
        corrupt file is treated as absent (warned, then re-fetched) — it is
        not "present" in the stale-but-present sense."""
        path = self._symbol_cache_path
        if path is None or not path.exists():
            return None
        try:
            cached = ExchangeSymbolSet.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            _logger.warning("binance: unreadable exchangeInfo cache at %s — refetching", path.name)
            return None
        return cached.symbols

    def _persist_symbols(self, symbol_set: ExchangeSymbolSet) -> None:
        path = self._symbol_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Hand-rolled dump so the symbol list is sorted on disk (deterministic
        # file content; pydantic would serialize the frozenset in hash order).
        path.write_text(
            json.dumps(
                {
                    "source": symbol_set.source,
                    "symbols": sorted(symbol_set.symbols),
                    "fetched_at": symbol_set.fetched_at.isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
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
                raise _classify_error(err, what=f"klines for {symbol}") from err
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

    def get_quote(self, symbol: str) -> Quote:
        """`QuoteSource` (ADR-0031): a live single-symbol quote from
        `GET /api/v3/ticker/24hr`. The membership-routed quote half of the
        Plan 0058 follow-up — the Yahoo quote endpoint 404s on Binance pairs,
        so a Binance-only symbol (e.g. `BTCUSDT`) had no live price before this.

        Raises the same typed taxonomy as `fetch_ohlcv` (451 →
        `GeoRestrictedError`, never retried; 429 → `RateLimitedError`; other
        upstream exhaustion → `UpstreamUnavailableError`) and
        `BinanceKlinesError` on a shape-broken payload; `ValueError` for an
        empty symbol."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        try:
            payload = self._http.get(
                _TICKER_24HR_URL, params={"symbol": symbol}, expect_json=True
            ).json()
        except ResilientHttpError as err:
            raise _classify_error(err, what=f"the 24h quote for {symbol}") from err
        return _ticker_to_quote(symbol, payload)


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


def _ticker_to_quote(symbol: str, payload: Any) -> Quote:
    """Map a `/api/v3/ticker/24hr` payload onto a validated `Quote`. `lastPrice`
    → price (enforced positive, the `fetch_ohlcv` rule); `priceChangePercent`
    → `change_pct`, already a percent (never scaled; may be negative);
    `closeTime` (epoch ms) → `as_of`. Day high/low + prev close + base-asset
    volume carry over when present (lenient — a missing optional field stays
    `None`, never sinks the quote); the 52-week range has no 24h-ticker
    analogue and stays `None`, and `market_state` stays "" (crypto has no
    Yahoo-style session phases). Currency is set only when unambiguous from the
    pair name (`*USDT`) — the ticker payload does not carry the quote asset, so
    a non-USDT pair is left "" rather than mislabelled. A non-object payload, a
    missing/non-positive `lastPrice`, a non-numeric `priceChangePercent`, or a
    missing `closeTime` is shape drift → `BinanceKlinesError`."""
    if not isinstance(payload, dict):
        raise BinanceKlinesError(f"binance: 24h ticker payload for {symbol} is not an object")
    price = _price(payload.get("lastPrice"), field="lastPrice", symbol=symbol)
    change_pct = _decimal_str(
        payload.get("priceChangePercent"), field="priceChangePercent", symbol=symbol
    )
    close_time = payload.get("closeTime")
    if isinstance(close_time, bool) or not isinstance(close_time, int):
        raise BinanceKlinesError(
            f"binance: 24h ticker for {symbol} missing integer 'closeTime'",
        )
    return Quote(
        symbol=symbol,
        price=price,
        as_of=datetime.fromtimestamp(close_time / 1000, tz=UTC),
        source=_SOURCE,
        change_pct=change_pct,
        previous_close=_optional_decimal(payload.get("prevClosePrice")),
        day_high=_optional_decimal(payload.get("highPrice")),
        day_low=_optional_decimal(payload.get("lowPrice")),
        currency="USDT" if symbol.endswith("USDT") else "",
        volume=_optional_decimal(payload.get("volume")),
    )


def _optional_decimal(value: Any) -> float | None:
    """Parse an optional upstream decimal-string field to `float`, or `None`
    when absent or unparseable. Unlike `_decimal_str` this never raises — for
    the quote's non-load-bearing fields (day range, prev close, volume), where
    a single odd field should not sink the whole quote."""
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_exchange_info(payload: Any) -> ExchangeSymbolSet:
    """Parse the `/api/v3/exchangeInfo` payload into the cached symbol set.
    Every listed pair is included regardless of trading status — a delisted
    pair still has historical klines, and Binance stays authoritative for its
    own names either way. `fetched_at` comes from the payload's `serverTime`
    (upstream's own clock). Shape drift raises `BinanceKlinesError`."""
    if not isinstance(payload, dict):
        raise BinanceKlinesError("binance: exchangeInfo payload is not an object")
    server_time = payload.get("serverTime")
    if isinstance(server_time, bool) or not isinstance(server_time, int):
        raise BinanceKlinesError(
            "binance: exchangeInfo payload missing integer 'serverTime'",
        )
    entries = payload.get("symbols")
    if not isinstance(entries, list):
        raise BinanceKlinesError("binance: exchangeInfo payload missing 'symbols' list")
    symbols: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("symbol"), str):
            raise BinanceKlinesError(
                "binance: exchangeInfo entry missing string 'symbol'",
            )
        symbols.add(entry["symbol"])
    return ExchangeSymbolSet(
        source="binance",
        symbols=frozenset(symbols),
        fetched_at=datetime.fromtimestamp(server_time / 1000, tz=UTC),
    )


def _classify_error(err: ResilientHttpError, *, what: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy. HTTP 451 → geo-restricted (ADR-0052: surfaced, never improvised
    around); 429 → rate-limited (carrying `Retry-After`); any other status or
    transport failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 451:
        return GeoRestrictedError(
            f"binance: geo-restricted (HTTP 451) fetching {what} — "
            f"api.binance.com is blocked from this network (ADR-0052)",
        )
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"binance: rate limited (HTTP 429) fetching {what}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"binance: upstream unavailable ({detail}) fetching {what}",
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
    "ExchangeSymbolSet",
]
