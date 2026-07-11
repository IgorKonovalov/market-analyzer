"""Coinbase Exchange spot market-data adapter — Plan 0081 phase 1 (ADR-0076).

Keyless OHLCV from `api.exchange.coinbase.com`
(`GET /products/{id}/candles`) through `CoinbaseHttpClient`, a
`ResilientHttpClient` subclass whose classifier pins the geo quirk shared with
Binance: **HTTP 451 is the geo-restriction response** and is `PERMANENT` — never
retried, surfaced as the typed `GeoRestrictedError` so the fallback decision is
made as an ADR-0052/ADR-0076 follow-up by a human, never improvised in the
adapter. (Coinbase Exchange public market data is expected US-clean — the whole
point of ADR-0076's geo hedge — but the pin costs nothing and keeps the never-
retry guarantee explicit if that ever changes.)

`fetch_ohlcv` implements `OhlcvSource` (ADR-0031) with absolute-window semantics:
the requested `[start, end]` window is walked **backward** in ≤300-candle pages
(the documented `/candles` per-request cap), advancing an `end` cursor past each
page's oldest candle. **An empty page is end-of-history, not an error** — as with
Binance (ADR-0052 Notes / ADR-0076), full-history-by-pagination is confirmed in
practice but not doc-guaranteed, so the terminator is the upstream running out of
rows. Coinbase omits empty buckets (no-trade intervals are absent rather than
zero-volume), so this terminator assumes no ≥300-bucket trade gap inside a listed
pair's history — true for the liquid USD pairs this serves.

Coinbase serves only `15m/1h/1d` natively (granularities `900/3600/86400`); the
coarse `4h/1w/1mo` timeframes are **derived on read** by the provider (Plan 0081
phase 2 / ADR-0028), never fetched here — a non-native timeframe is a caller bug
and raises `ValueError`, exactly like the Yahoo adapter rejects a resampled
timeframe.

The `/candles` wire shape is `[time, low, high, open, close, volume]` with `time`
in **epoch seconds** and OHLCV as JSON **numbers** (contrast Binance's
string-encoded klines) — mapped onto the existing `Bar`. A zero/negative price is
upstream garbage and raises the typed `CoinbaseError`. An empty *overall*
response is classified by window recency per ADR-0033 (identically to the Yahoo
and Binance adapters): a leading-edge empty window means the symbol is unknown; a
strictly-historical empty window is a legitimate end-of-history and returns `[]`.

`get_quote` maps `GET /products/{id}/ticker` (string-encoded `price`, ISO `time`)
onto the project `Quote` with `currency="USD"` — Coinbase quotes crypto in USD
natively (ADR-0076). `search` / `is_known_symbol` route over the cached product
set from `GET /products` (mirror `binance_klines.py`'s `ExchangeSymbolSet`
design: explicit `refresh_symbols()` or a missing cache reaches the network; a
present cache is used as-is however stale — stale-but-present beats absent). The
product payload carries no server timestamp, so `fetched_at` is read from the
HTTP `Date` response header (the upstream's clock, not a local wall-clock read);
absent that header it falls back to the epoch, harmless because there is no TTL.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol / composition root, never by importing this class.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote as urlquote

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

_BASE_URL = "https://api.exchange.coinbase.com"
_PRODUCTS_URL = f"{_BASE_URL}/products"
_SOURCE = "coinbase"

# Upstream page cap for /products/{id}/candles (ADR-0076 verified facts: max 300
# candles per request). The backward walk requests windows spanning at most this
# many buckets inclusive.
_PAGE_LIMIT = 300

# Canonical timeframe → Coinbase granularity (seconds). ONLY the natively-served
# granularities: Coinbase's set is {60, 300, 900, 3600, 21600, 86400}; the
# canonical registry's 15m/1h/1d land on 900/3600/86400. The coarse 4h/1w/1mo are
# derived on read by the provider (ADR-0028), never fetched here — so they are
# deliberately absent and a request for one raises ValueError (a caller bug),
# mirroring the Yahoo adapter's rejection of a resampled timeframe.
_COINBASE_GRANULARITY: dict[str, int] = {
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}

# A candle array's fields, per the official /candles docs:
# [0] time (epoch s), [1] low, [2] high, [3] open, [4] close, [5] volume.
# Note the low/high/open/close order — NOT Binance's open/high/low/close.
_CANDLE_MIN_FIELDS = 6

# Result cap for symbol search, mirroring the Binance/Yahoo adapters: the picker
# only ever shows a handful of suggestions.
_MAX_SEARCH_RESULTS = 10

# The source label search results carry (`SymbolInfo.exchange`) so the picker can
# tell `BTC-USD` (Coinbase, deep USD) from `BTCUSDT` (Binance, USDT) from a Yahoo
# composite — three distinct series, never presented as interchangeable
# (ADR-0052 / ADR-0076 / Plan 0081 phase 4).
_SEARCH_EXCHANGE_LABEL = "Coinbase"
_SEARCH_QUOTE_TYPE = "Cryptocurrency"


class CoinbaseProductSet(BaseModel):
    """The cached `/products` id universe driving provider dispatch (Plan 0081
    phase 2 / ADR-0076: a symbol routes to Coinbase iff it is in this set — no
    prefixes, no format heuristics). `fetched_at` is the HTTP `Date` response
    header (the upstream's clock; the products payload carries no timestamp of
    its own), so staleness is visible and refresh stays explicit."""

    model_config = ConfigDict(frozen=True)

    source: Literal["coinbase"]
    symbols: frozenset[str]
    fetched_at: datetime


class CoinbaseError(ValueError):
    """The upstream 2xx payload broke shape (non-list body, malformed candle
    array, non-numeric field, a zero/negative price, or a non-advancing page
    cursor) — raised at the adapter boundary before anything reaches the cache.
    Upstream drift surfaces typed, never as a silently-skipped bar."""


class CoinbaseHttpClient(ResilientHttpClient):
    """`ResilientHttpClient` that pins Coinbase's geo-restriction response.

    HTTP 451 means the caller's network is geo-blocked — a structural condition,
    not a transient fault. The base classifier already treats non-429 4xx as
    `PERMANENT`; the explicit branch makes the never-retry guarantee independent
    of the base policy (the same pin as `BinanceSpotHttpClient`)."""

    def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
        if response is not None and response.status_code == 451:
            return ErrorKind.PERMANENT
        return super().classify(exc, response)


class CoinbaseAdapter:
    """Fetches Coinbase spot OHLCV bars (`OhlcvSource`, ADR-0031), resolves the
    symbol search (`SymbolSearchSource`), serves live quotes (`QuoteSource`), and
    owns the cached `/products` id set that drives provider dispatch (ADR-0076
    membership routing). The capability breadth mirrors `BinanceKlinesAdapter`:
    this class already owns the HTTP client and the membership set, so a separate
    per-capability adapter would only duplicate both.

    `symbol_cache_path` is where the product set persists across sessions (wired
    by the composition root — `<data-dir>/coinbase_products.json`); `None` keeps
    the set process-memoized only. **Stale-but-present beats absent**: a cached
    set is used as-is with no TTL and no auto-refresh — only `refresh_symbols()`
    (or a missing cache) reaches the network, so a newly-listed pair misroutes to
    Yahoo (loud/shallow) until an explicit refresh, the accepted Plan 0081
    trade."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        symbol_cache_path: Path | None = None,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else CoinbaseHttpClient(
                source_name=_SOURCE,
                # History pages are one-shot reads persisted in the SQLite bars
                # cache; an in-memory TTL cache buys nothing.
                cache_ttl_seconds=0.0,
            )
        )
        self._symbol_cache_path = symbol_cache_path
        self._symbols: frozenset[str] | None = None

    def is_known_symbol(self, symbol: str) -> bool:
        """Whether `symbol` is in the cached Coinbase product universe — the
        ADR-0076 membership test the provider routes OHLCV by."""
        return symbol.strip().upper() in self.known_symbols()

    def known_symbols(self) -> frozenset[str]:
        """The cached `/products` id set: process memo, else the cache file (used
        as-is however stale — stale-but-present beats absent), else one lazy
        fetch-and-persist. If that lazy fetch fails, the failure is memoized as an
        **empty set for this process** (a warning is logged): routing degrades to
        Yahoo-for-everything — loud/shallow for Coinbase-only symbols, the same
        failure shape as the plan's accepted stale-set misroute — rather than
        failing every non-Coinbase request or re-probing a dead upstream per
        call. `refresh_symbols()` (or a process restart) recovers."""
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
                "coinbase: /products unavailable (%s) — no symbols will route to "
                "Coinbase until refresh_symbols() succeeds or the process restarts",
                type(err).__name__,
            )
            self._symbols = frozenset()
            return self._symbols

    def refresh_symbols(self) -> frozenset[str]:
        """Explicitly re-fetch `GET /products`, update the process memo, and
        persist the set to the cache file (when one is wired). The one refresh
        path of the Plan 0081 risk note. Raises the same typed taxonomy as
        `fetch_ohlcv` (451 → `GeoRestrictedError`, never retried) and
        `CoinbaseError` on a shape-broken payload."""
        try:
            response = self._http.get(_PRODUCTS_URL, expect_json=True)
        except ResilientHttpError as err:
            raise _classify_error(err, what="the product set") from err
        product_set = _parse_products(response.json(), fetched_at=_response_date(response.headers))
        self._symbols = product_set.symbols
        self._persist_symbols(product_set)
        return product_set.symbols

    def search(self, query: str) -> list[SymbolInfo]:
        """`SymbolSearchSource` (ADR-0031) over the cached product set (Plan 0081
        phase 4): case-insensitive substring match against the product ids, exact
        match first, then prefix matches, then the rest — each group
        alphabetical, capped at `_MAX_SEARCH_RESULTS`. Results carry the Coinbase
        source label (`exchange`) so the picker never presents `BTC-USD` and
        `BTCUSDT` as interchangeable (ADR-0052 / ADR-0076). Every hit is fetchable
        by `get_ohlcv` by construction — it is in the same membership set the
        provider routes by (the ADR-0026 chartable invariant).

        An empty/whitespace query short-circuits to `[]`; a zero-match query also
        returns `[]` (not an error). Deterministic: pure sorts over the set, no
        set-iteration order leaks into the result."""
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
        """The persisted product set, or `None` when there is no cache file. A
        corrupt file is treated as absent (warned, then re-fetched) — it is not
        "present" in the stale-but-present sense."""
        path = self._symbol_cache_path
        if path is None or not path.exists():
            return None
        try:
            cached = CoinbaseProductSet.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            _logger.warning("coinbase: unreadable products cache at %s — refetching", path.name)
            return None
        return cached.symbols

    def _persist_symbols(self, product_set: CoinbaseProductSet) -> None:
        path = self._symbol_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Hand-rolled dump so the id list is sorted on disk (deterministic file
        # content; pydantic would serialize the frozenset in hash order).
        path.write_text(
            json.dumps(
                {
                    "source": product_set.source,
                    "symbols": sorted(product_set.symbols),
                    "fetched_at": product_set.fetched_at.isoformat(),
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
        """Raw candles for the absolute `[start, end]` window, paginated backward
        at ≤300 candles/page, deduplicated by candle time and returned in
        ascending timestamp order. Empty page = end-of-history (ADR-0076); an
        empty overall response is classified by window recency (ADR-0033 — see the
        module docstring).

        Raises `GeoRestrictedError` on HTTP 451 (never retried), `RateLimitedError`
        on 429, `UpstreamUnavailableError` on other upstream exhaustion,
        `UnknownSymbolError` on a leading-edge empty window, `CoinbaseError` on a
        shape-broken payload, and `ValueError` for caller bugs (non-native/bad
        timeframe, naive datetimes, inverted window)."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        granularity = _coinbase_granularity(timeframe)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")
        if start >= end:
            raise ValueError(f"start ({start}) must be strictly before end ({end})")

        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        step = timedelta(seconds=granularity)
        # A page spans at most (limit - 1) steps inclusive, so [window_start,
        # cursor_end] yields ≤ _PAGE_LIMIT buckets.
        page_span = step * (_PAGE_LIMIT - 1)
        candles_url = f"{_BASE_URL}/products/{urlquote(symbol, safe='')}/candles"

        bars_by_ts: dict[int, Bar] = {}
        cursor_end = end_utc
        while cursor_end >= start_utc:
            window_start = max(start_utc, cursor_end - page_span)
            params: dict[str, str | int | float] = {
                "granularity": granularity,
                "start": window_start.isoformat(),
                "end": cursor_end.isoformat(),
            }
            try:
                payload = self._http.get(candles_url, params=params, expect_json=True).json()
            except ResilientHttpError as err:
                raise _classify_error(err, what=f"candles for {symbol}") from err
            page = _parse_page(payload, symbol=symbol, timeframe=timeframe)
            if not page:
                break  # empty page = end-of-history, not an error (ADR-0076)
            for ts_sec, bar in page:
                stored = bars_by_ts.get(ts_sec)
                if stored is not None and stored != bar:
                    raise CoinbaseError(
                        f"coinbase: two candles for {symbol} ({timeframe}) at time "
                        f"{ts_sec} with different values",
                    )
                bars_by_ts[ts_sec] = bar
            oldest_ts = min(ts for ts, _ in page)
            next_end = datetime.fromtimestamp(oldest_ts, tz=UTC) - step
            if next_end >= cursor_end:
                raise CoinbaseError(
                    f"coinbase: candles page for {symbol} did not advance past "
                    f"end={cursor_end.isoformat()} (oldest time={oldest_ts}) — refusing to loop",
                )
            cursor_end = next_end

        bars = [
            bar for _, bar in sorted(bars_by_ts.items()) if start_utc <= bar.event_ts <= end_utc
        ]
        if not bars:
            # ADR-0033: classify emptiness by window recency, mirroring the Yahoo
            # and Binance adapters — at the leading edge a live, listed pair must
            # have data, so an empty answer there means the symbol is unknown; a
            # strictly-historical empty window is a legitimate end-of-history and
            # returns []. `now` comes from the provider's `_now`/`as_of` seam;
            # absent it, the conservative leading-edge reading applies.
            if _reaches_leading_edge(end_utc, timeframe, now):
                raise UnknownSymbolError(
                    f"coinbase: no candles for {symbol!r} over "
                    f"[{start_utc.isoformat()}, {end_utc.isoformat()}] ({timeframe}) — "
                    f"symbol is likely unknown or unlisted",
                    symbol=symbol,
                )
            return []
        return bars

    def get_quote(self, symbol: str) -> Quote:
        """`QuoteSource` (ADR-0031): a live single-symbol quote from
        `GET /products/{id}/ticker`, mapped onto `Quote(currency="USD")` — Coinbase
        quotes crypto in USD natively (ADR-0076).

        Raises the same typed taxonomy as `fetch_ohlcv` (451 → `GeoRestrictedError`,
        never retried; 429 → `RateLimitedError`; other upstream exhaustion →
        `UpstreamUnavailableError`) and `CoinbaseError` on a shape-broken payload;
        `ValueError` for an empty symbol."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        ticker_url = f"{_BASE_URL}/products/{urlquote(symbol, safe='')}/ticker"
        try:
            payload = self._http.get(ticker_url, expect_json=True).json()
        except ResilientHttpError as err:
            raise _classify_error(err, what=f"the quote for {symbol}") from err
        return _ticker_to_quote(symbol, payload)


def _coinbase_granularity(timeframe: str) -> int:
    """The Coinbase `granularity` (seconds) for a canonical timeframe. Validates
    against the canonical registry first (an unknown timeframe is a caller bug →
    `ValueError`), then rejects a registered-but-derived timeframe (4h/1w/1mo) as
    not natively fetchable — the provider resamples those on read (ADR-0028),
    mirroring the Yahoo adapter's `require_native_interval`."""
    timeframe_spec(timeframe)  # raises ValueError for an unregistered timeframe
    try:
        return _COINBASE_GRANULARITY[timeframe]
    except KeyError:
        raise ValueError(
            f"timeframe {timeframe!r} is not natively fetchable from Coinbase "
            "(derived on read via resampling)",
        ) from None


def _reaches_leading_edge(end: datetime, timeframe: str, now: datetime | None) -> bool:
    """Whether the requested window's `end` reaches the leading edge — within one
    bar of `now` (ADR-0033). Identical reading to the Yahoo/Binance adapters'."""
    if now is None:
        return True
    return end >= now - bar_duration(timeframe)


def _parse_page(
    payload: Any,
    *,
    symbol: str,
    timeframe: str,
) -> list[tuple[int, Bar]]:
    """Parse one `/candles` page into `(time_sec, bar)` pairs. Coinbase returns
    candles newest-first, but order does not matter here — the caller keys by
    time and re-sorts. Shape drift and zero/negative prices raise
    `CoinbaseError`."""
    if not isinstance(payload, list):
        raise CoinbaseError(
            f"coinbase: candles payload for {symbol} ({timeframe}) is not a list",
        )
    page: list[tuple[int, Bar]] = []
    for entry in payload:
        if not isinstance(entry, list) or len(entry) < _CANDLE_MIN_FIELDS:
            raise CoinbaseError(
                f"coinbase: malformed candle array for {symbol} ({timeframe})",
            )
        time_sec = entry[0]
        if isinstance(time_sec, bool) or not isinstance(time_sec, int):
            raise CoinbaseError(
                f"coinbase: candle for {symbol} ({timeframe}) missing integer time",
            )
        low = _price(entry[1], field="low", symbol=symbol)
        high = _price(entry[2], field="high", symbol=symbol)
        open_ = _price(entry[3], field="open", symbol=symbol)
        close = _price(entry[4], field="close", symbol=symbol)
        volume = _number(entry[5], field="volume", symbol=symbol)
        if volume < 0:
            raise CoinbaseError(
                f"coinbase: negative volume {volume!r} for {symbol} ({timeframe})",
            )
        page.append(
            (
                time_sec,
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_ts=datetime.fromtimestamp(time_sec, tz=UTC),
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


def _number(value: Any, *, field: str, symbol: str) -> float:
    """Parse a JSON-numeric candle field (Coinbase encodes candle OHLCV as
    numbers, not strings) to `float`. A bool, non-number, or NaN/Inf is shape
    drift → `CoinbaseError`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoinbaseError(
            f"coinbase: candle for {symbol} missing numeric {field!r}",
        )
    if not math.isfinite(value):
        raise CoinbaseError(
            f"coinbase: non-finite {field} {value!r} for {symbol}",
        )
    return float(value)


def _price(value: Any, *, field: str, symbol: str) -> float:
    """Parse a numeric candle price and enforce the Plan 0081 phase-1 rule: a
    zero or negative price is upstream garbage, raised typed — never a bar."""
    parsed = _number(value, field=field, symbol=symbol)
    if parsed <= 0:
        raise CoinbaseError(
            f"coinbase: non-positive {field} price {parsed!r} for {symbol}",
        )
    return parsed


def _ticker_to_quote(symbol: str, payload: Any) -> Quote:
    """Map a `/products/{id}/ticker` payload onto a validated `Quote`. `price`
    (string-encoded, enforced positive) → price; `time` (ISO 8601) → `as_of`;
    `volume` (string-encoded, lenient) carries over when present. Currency is
    `USD` — Coinbase quotes crypto in USD natively (ADR-0076); the ticker endpoint
    has no 24h change / day-range / prev-close analogue, so those stay `None` and
    `market_state` stays "". A non-object payload, a missing/non-positive `price`,
    or a missing/unparseable `time` is shape drift → `CoinbaseError`."""
    if not isinstance(payload, dict):
        raise CoinbaseError(f"coinbase: ticker payload for {symbol} is not an object")
    price = _decimal_str(payload.get("price"), field="price", symbol=symbol)
    if price <= 0:
        raise CoinbaseError(f"coinbase: non-positive price {price!r} for {symbol}")
    as_of = _parse_iso_time(payload.get("time"), symbol=symbol)
    return Quote(
        symbol=symbol,
        price=price,
        as_of=as_of,
        source=_SOURCE,
        currency="USD",
        volume=_optional_decimal(payload.get("volume")),
    )


def _decimal_str(value: Any, *, field: str, symbol: str) -> float:
    """Parse an upstream string-encoded decimal (the ticker wire encoding for
    price/volume) to `float`. Anything else is shape drift."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise CoinbaseError(
        f"coinbase: ticker for {symbol} missing decimal-string {field!r}",
    )


def _optional_decimal(value: Any) -> float | None:
    """Parse an optional upstream decimal-string field to `float`, or `None` when
    absent or unparseable. Never raises — for the quote's non-load-bearing volume
    field, where a single odd value should not sink the whole quote."""
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_iso_time(value: Any, *, symbol: str) -> datetime:
    """Parse the ticker's ISO-8601 `time` (e.g. `2024-01-01T00:00:00.123456Z`) to
    a UTC-aware datetime. A missing/non-string/unparseable value is shape drift →
    `CoinbaseError`."""
    if not isinstance(value, str):
        raise CoinbaseError(f"coinbase: ticker for {symbol} missing string 'time'")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CoinbaseError(
            f"coinbase: ticker for {symbol} has unparseable 'time' {value!r}",
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_products(payload: Any, *, fetched_at: datetime) -> CoinbaseProductSet:
    """Parse the `GET /products` payload into the cached id set. Every listed
    product is included regardless of trading status — a delisted pair still has
    historical candles, and Coinbase stays authoritative for its own ids either
    way. Shape drift raises `CoinbaseError`."""
    if not isinstance(payload, list):
        raise CoinbaseError("coinbase: /products payload is not a list")
    ids: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise CoinbaseError("coinbase: /products entry missing string 'id'")
        ids.add(entry["id"])
    return CoinbaseProductSet(
        source="coinbase",
        symbols=frozenset(ids),
        fetched_at=fetched_at,
    )


def _response_date(headers: dict[str, str]) -> datetime:
    """The HTTP `Date` response header as a UTC datetime — the upstream's own
    clock, used for the product set's `fetched_at` because the `/products`
    payload carries no timestamp of its own (never a local wall-clock read). An
    absent/unparseable header falls back to the epoch: freshness is unknown but
    harmless, since the product cache has no TTL (stale-but-present beats
    absent)."""
    raw = _header(headers, "Date")
    if raw is None:
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _classify_error(err: ResilientHttpError, *, what: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy. HTTP 451 → geo-restricted (surfaced, never improvised around);
    429 → rate-limited (carrying `Retry-After`); any other status or transport
    failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 451:
        return GeoRestrictedError(
            f"coinbase: geo-restricted (HTTP 451) fetching {what} — "
            f"api.exchange.coinbase.com is blocked from this network (ADR-0076)",
        )
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"coinbase: rate limited (HTTP 429) fetching {what}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"coinbase: upstream unavailable ({detail}) fetching {what}",
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
    "CoinbaseAdapter",
    "CoinbaseError",
    "CoinbaseHttpClient",
    "CoinbaseProductSet",
]
