"""Plan 0081 phase 1 — Coinbase spot candles adapter: fetch + pagination + quote + search.

Phase-1 done-when claims pinned here:
(a) a >300-bar fixture window returns a contiguous, gap-free, deduplicated,
    ascending series — the backward-paged walk assembled correctly across the
    300-candle page cap;
(b) an empty page terminates the walk cleanly (a window reaching before listing);
(c) the `[start, end]` filter is inclusive of both ends;
(d) a malformed candle row (short array / non-numeric / zero-or-negative price)
    raises the typed `CoinbaseError`, never silently passes;
(e) a 451 fixture raises `GeoRestrictedError` — exactly one transport attempt
    (classified permanent, not a retry);
(f) `get_quote` maps to `Quote(currency="USD")`;
(g) `search` / `is_known_symbol` resolve against a fixture product set with no
    set-iteration order leak (deterministic).

Plus the ADR-0033 empty-window split (leading-edge empty → `UnknownSymbolError`,
strictly-historical empty → `[]`), the non-native-timeframe rejection, and the
typed-error taxonomy (429 / 5xx).

Fixture provenance: candle arrays are the documented `/candles` wire shape —
`[time, low, high, open, close, volume]` with `time` an epoch-second integer and
OHLCV as JSON numbers (contrast Binance's string-encoded klines), served
newest-first. The 451 body mirrors a real restricted-location response. The fake
transport honors the wire contract: `start`/`end` (ISO 8601) filter by candle
time inclusively and the response is capped at the 300-candle page limit,
descending. All offline — no live api.exchange.coinbase.com.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.coinbase import (
    CoinbaseAdapter,
    CoinbaseError,
    CoinbaseHttpClient,
)
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UnknownSymbolError,
    UpstreamUnavailableError,
)
from market_analyser.data.sources import OhlcvSource, QuoteSource, SymbolSearchSource

# 2024-01-01T00:00:00Z — 15m-aligned fixture anchor.
_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_BASE_TS = int(_BASE.timestamp())
_STEP = 900  # 15m in seconds
# >300 bars so the backward walk crosses the 300-candle page cap (2 data pages).
_HISTORY_BARS = 350
# The fake upstream serves at most this many candles per request — the real
# /candles per-request cap.
_SERVER_PAGE_CAP = 300

# A real Coinbase restricted-location response body (HTTP 451).
_BODY_451 = json.dumps({"message": "Service unavailable in your region"}).encode("utf-8")


def _bar_ts(index: int) -> int:
    return _BASE_TS + index * _STEP


def _candle(index: int) -> list[Any]:
    """One candle in the documented `[time, low, high, open, close, volume]`
    wire shape (numeric fields), with the OHLC invariants intact."""
    return [
        _bar_ts(index),
        99.0 + index,  # low
        101.5 + index,  # high
        100.0 + index,  # open
        100.5 + index,  # close
        10.0 + index,  # volume
    ]


def _history_candles() -> list[list[Any]]:
    """`_HISTORY_BARS` 15m candles with bar 4 duplicated verbatim (same time,
    same values) — the shape the dedup must collapse."""
    candles = [_candle(i) for i in range(_HISTORY_BARS)]
    candles.insert(5, _candle(4))
    return candles


class _FakeTransport:
    """Replaces `ResilientHttpClient._perform_request` (the transport seam),
    routing by URL: `/candles` serves candles with time in `[start, end]`
    (parsed from the ISO params), newest-first, capped at the 300-candle page
    limit; `/ticker` serves the configured quote; `/products` serves the
    configured id set with a `Date` header. Records every candles request's
    query params."""

    def __init__(
        self,
        *,
        candles: list[list[Any]] | None = None,
        products: list[dict[str, Any]] | None = None,
        ticker: dict[str, Any] | None = None,
        products_date: str | None = "Mon, 01 Jan 2024 00:00:00 GMT",
    ) -> None:
        self._candles = candles if candles is not None else []
        self._products = products if products is not None else []
        self._ticker = ticker if ticker is not None else {}
        self._products_date = products_date
        self.candles_requests: list[dict[str, str]] = []
        self.products_requests = 0

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        split = urllib.parse.urlsplit(url)
        raw_query = urllib.parse.parse_qs(split.query)
        query = {k: v[0] for k, v in raw_query.items()}
        resp_headers: dict[str, str] = {}
        if "/candles" in split.path:
            self.candles_requests.append(query)
            start_sec = int(datetime.fromisoformat(query["start"]).timestamp())
            end_sec = int(datetime.fromisoformat(query["end"]).timestamp())
            page = [c for c in self._candles if start_sec <= int(c[0]) <= end_sec]
            page.sort(key=lambda c: c[0], reverse=True)  # newest-first
            payload: Any = page[:_SERVER_PAGE_CAP]
        elif split.path.endswith("/ticker"):
            payload = self._ticker
        else:  # /products
            self.products_requests += 1
            payload = self._products
            if self._products_date is not None:
                resp_headers["Date"] = self._products_date
        return HttpResponse(
            status_code=200,
            headers=resp_headers,
            body=json.dumps(payload).encode("utf-8"),
            elapsed_seconds=0.0,
        )


def _static_response(status_code: int, body: bytes, headers: dict[str, str] | None = None) -> Any:
    """A transport fake that always returns one response, counting attempts."""

    class _Static:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(
            self, method: str, url: str, req_body: Any, req_headers: Any, *, proxy: Any
        ) -> HttpResponse:
            self.attempts += 1
            return HttpResponse(
                status_code=status_code,
                headers=headers or {},
                body=body,
                elapsed_seconds=0.0,
            )

    return _Static()


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any | None = None,
    max_retries: int = 0,
) -> tuple[CoinbaseAdapter, Any]:
    client = CoinbaseHttpClient(
        source_name="coinbase-test", cache_ttl_seconds=0.0, max_retries=max_retries
    )
    fake = transport if transport is not None else _FakeTransport(candles=_history_candles())
    monkeypatch.setattr(client, "_perform_request", fake)
    return CoinbaseAdapter(http_client=client), fake


def _window(first_bar: int, last_bar: int) -> tuple[datetime, datetime]:
    return (
        datetime.fromtimestamp(_bar_ts(first_bar), tz=UTC),
        datetime.fromtimestamp(_bar_ts(last_bar), tz=UTC),
    )


# A `now` far past the fixture so every test window is strictly historical unless
# the leading-edge classification is exercised explicitly.
_LONG_AFTER = _BASE + timedelta(days=365)


# --- contract -------------------------------------------------------------------


def test_adapter_satisfies_source_protocols() -> None:
    adapter = CoinbaseAdapter()
    assert isinstance(adapter, OhlcvSource)
    assert isinstance(adapter, QuoteSource)
    assert isinstance(adapter, SymbolSearchSource)


def test_granularity_map_is_native_only() -> None:
    """Coinbase serves 15m/1h/1d natively; 4h/1w/1mo are derived on read and are
    deliberately absent from the granularity map."""
    from market_analyser.data.adapters.coinbase import _COINBASE_GRANULARITY

    assert _COINBASE_GRANULARITY == {"15m": 900, "1h": 3600, "1d": 86400}


# --- (a) pagination: >300 bars, contiguous, deduplicated, ascending --------------


def test_over_300_bar_window_returns_contiguous_deduplicated_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, fake = _adapter(monkeypatch)
    start, end = _window(0, _HISTORY_BARS - 1)

    bars = adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)

    # Deduplicated: the fixture's 351 rows (bar 4 twice) yield 350 bars.
    assert len(bars) == _HISTORY_BARS
    assert len({b.event_ts for b in bars}) == _HISTORY_BARS
    # Contiguous and gap-free: exact 15m spacing from the anchor, ascending.
    assert bars[0].event_ts == _BASE
    assert all(
        later.event_ts - earlier.event_ts == timedelta(minutes=15)
        for earlier, later in pairwise(bars)
    )
    # Values come straight from the fixture; provenance pinned.
    assert [b.close for b in bars] == [100.5 + i for i in range(_HISTORY_BARS)]
    assert all(b.symbol == "BTC-USD" for b in bars)
    assert all(b.timeframe == "15m" for b in bars)
    assert all(b.source == "coinbase" for b in bars)
    # Two data pages (300 + 50) across the 300-candle cap; the cursor-past-start
    # termination ends the walk, so no extra empty request is needed.
    assert len(fake.candles_requests) == 2
    assert all(req["granularity"] == "900" for req in fake.candles_requests)


def test_candle_field_order_is_low_high_open_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coinbase's `[time, low, high, open, close, volume]` order (NOT
    open/high/low/close) is mapped onto the right Bar fields."""
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[_candle(0)]))
    start, end = _window(0, 1)

    (bar,) = adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)

    assert (bar.low, bar.high, bar.open, bar.close, bar.volume) == (99.0, 101.5, 100.0, 100.5, 10.0)


# --- (b) empty page terminates the walk cleanly ----------------------------------


def test_empty_page_before_listing_terminates_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window whose start reaches before the earliest candle: the walk pages
    back through the data, then the pre-listing window returns an empty page,
    which terminates without error (ADR-0076)."""
    candles = [_candle(i) for i in range(10)]
    adapter, fake = _adapter(monkeypatch, transport=_FakeTransport(candles=candles))
    # Start 400 buckets before bar 0 — well before listing.
    start = datetime.fromtimestamp(_bar_ts(0) - 400 * _STEP, tz=UTC)
    _, end = _window(0, 9)

    bars = adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)

    assert [int(b.event_ts.timestamp()) for b in bars] == [_bar_ts(i) for i in range(10)]
    # The last request covered the pre-listing window and came back empty.
    assert len(fake.candles_requests) >= 2


# --- (c) inclusive [start, end] filter -------------------------------------------


def test_window_filter_is_inclusive_of_both_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    start, end = _window(3, 4)  # exactly two bar timestamps

    bars = adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)

    assert [int(b.event_ts.timestamp()) for b in bars] == [_bar_ts(3), _bar_ts(4)]


# --- (d) bad rows raise typed validation errors ----------------------------------


@pytest.mark.parametrize("bad_price", [0.0, -1.5])
def test_zero_or_negative_price_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, bad_price: float
) -> None:
    candle = _candle(0)
    candle[4] = bad_price  # close
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[candle]))
    start, end = _window(0, 1)

    with pytest.raises(CoinbaseError, match="non-positive close"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)


def test_non_numeric_price_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    candle = _candle(0)
    candle[3] = "not-a-number"  # open as a string — candles are numeric
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[candle]))
    start, end = _window(0, 1)

    with pytest.raises(CoinbaseError, match="open"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)


def test_truncated_candle_array_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    candle = _candle(0)[:5]  # volume missing
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[candle]))
    start, end = _window(0, 1)

    with pytest.raises(CoinbaseError, match="malformed candle"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)


def test_non_list_candles_payload_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _static_response(200, json.dumps({"message": "Invalid granularity"}).encode())
    adapter, _ = _adapter(monkeypatch, transport=fake)
    start, end = _window(0, 1)

    with pytest.raises(CoinbaseError, match="not a list"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)


def test_duplicate_time_with_different_values_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same time, different values is upstream drift — surfaced typed, never
    silently collapsed to either candle."""
    drifted = _candle(1)
    drifted[4] = 555.0  # close
    drifted[2] = 556.0  # high, keep high >= close so only the dedup trips
    adapter, _ = _adapter(
        monkeypatch, transport=_FakeTransport(candles=[_candle(0), _candle(1), drifted])
    )
    start, end = _window(0, 2)

    with pytest.raises(CoinbaseError, match="different values"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)


# --- (e) HTTP 451 → GeoRestrictedError, exactly one attempt ----------------------


def test_451_raises_geo_restricted_error_not_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # max_retries=3: retries are available, so one attempt proves 451 is
    # classified permanent, not that the budget ran out.
    client = CoinbaseHttpClient(source_name="coinbase-test", cache_ttl_seconds=0.0, max_retries=3)
    fake = _static_response(451, _BODY_451)
    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = CoinbaseAdapter(http_client=client)
    start, end = _window(0, 1)

    with pytest.raises(GeoRestrictedError, match="451") as excinfo:
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)

    assert excinfo.type is GeoRestrictedError
    assert fake.attempts == 1  # not a retry
    assert client.stats().retries == 0


def test_429_maps_to_rate_limited_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _static_response(429, b"{}", headers={"Retry-After": "30"})
    adapter, _ = _adapter(monkeypatch, transport=fake)
    start, end = _window(0, 1)

    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)
    assert excinfo.value.retry_after_seconds == 30


def test_5xx_maps_to_upstream_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _static_response(503, b"")
    adapter, _ = _adapter(monkeypatch, transport=fake)
    start, end = _window(0, 1)

    with pytest.raises(UpstreamUnavailableError, match="HTTP 503"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=_LONG_AFTER)


# --- (f) get_quote → Quote(currency="USD") ---------------------------------------


def test_get_quote_maps_to_usd_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker = {
        "trade_id": 123,
        "price": "50000.12",
        "size": "0.01",
        "time": "2024-01-01T00:00:00.123456Z",
        "bid": "49999.00",
        "ask": "50001.00",
        "volume": "1234.5",
    }
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(ticker=ticker))

    quote = adapter.get_quote("BTC-USD")

    assert quote.symbol == "BTC-USD"
    assert quote.price == 50000.12
    assert quote.currency == "USD"
    assert quote.source == "coinbase"
    assert quote.volume == 1234.5
    assert quote.as_of == datetime(2024, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)


def test_get_quote_non_positive_price_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker = {"price": "0", "time": "2024-01-01T00:00:00Z"}
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(ticker=ticker))

    with pytest.raises(CoinbaseError, match="non-positive price"):
        adapter.get_quote("BTC-USD")


def test_get_quote_missing_time_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker = {"price": "50000.00"}
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(ticker=ticker))

    with pytest.raises(CoinbaseError, match="'time'"):
        adapter.get_quote("BTC-USD")


# --- (g) search / membership over the product set --------------------------------


def _products_fixture() -> list[dict[str, Any]]:
    return [
        {"id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD"},
        {"id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"},
        {"id": "SOL-USD", "base_currency": "SOL", "quote_currency": "USD"},
        {"id": "ETH-BTC", "base_currency": "ETH", "quote_currency": "BTC"},
    ]


def test_membership_over_lazily_fetched_products(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch, transport=_FakeTransport(products=_products_fixture()))

    assert adapter.is_known_symbol("BTC-USD")
    assert adapter.is_known_symbol(" btc-usd ")  # normalized like fetch_ohlcv
    assert adapter.is_known_symbol("ETH-BTC")
    assert not adapter.is_known_symbol("BTCUSDT")  # never aliased to Binance (ADR-0076)
    assert not adapter.is_known_symbol("AAPL")
    assert fake.products_requests == 1  # one lazy fetch, then memoized


def test_search_ranks_exact_then_prefix_then_rest_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    products = [*_products_fixture(), {"id": "WBTC-USD"}, {"id": "BTC-USDC"}]
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(products=products))

    results = adapter.search("btc-usd")
    symbols = [r.symbol for r in results]

    # Exact first, then prefix matches (alphabetical), then substring rest.
    assert symbols[0] == "BTC-USD"
    assert symbols[1] == "BTC-USDC"  # prefix match
    assert "WBTC-USD" in symbols  # substring (rest)
    assert symbols.index("BTC-USDC") < symbols.index("WBTC-USD")
    # Every result carries the Coinbase source label (ADR-0076).
    assert all(r.exchange == "Coinbase" for r in results)

    # Deterministic: same query, same order across calls (no set-iteration leak).
    assert [r.symbol for r in adapter.search("btc-usd")] == symbols


def test_search_empty_and_zero_match_queries_return_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(products=_products_fixture()))

    assert adapter.search("   ") == []
    assert adapter.search("NOMATCH") == []


# --- ADR-0033: empty-window classification by recency ----------------------------


def test_leading_edge_empty_window_raises_unknown_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[]))
    start, end = _window(0, 5)

    with pytest.raises(UnknownSymbolError) as excinfo:
        adapter.fetch_ohlcv("NOPE-USD", "15m", start, end, now=end)
    assert excinfo.value.symbol == "NOPE-USD"


def test_strictly_historical_empty_window_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[]))
    start, end = _window(0, 5)

    bars = adapter.fetch_ohlcv("BTC-USD", "15m", start, end, now=end + timedelta(hours=2))

    assert bars == []


def test_no_now_reference_keeps_the_conservative_leading_edge_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(candles=[]))
    start, end = _window(0, 5)

    with pytest.raises(UnknownSymbolError):
        adapter.fetch_ohlcv("BTC-USD", "15m", start, end)


# --- input boundary ----------------------------------------------------------------


def test_caller_bugs_raise_value_error_before_any_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch)
    start, end = _window(0, 5)

    with pytest.raises(ValueError, match="non-empty"):
        adapter.fetch_ohlcv("  ", "15m", start, end)
    with pytest.raises(ValueError, match="unknown timeframe"):
        adapter.fetch_ohlcv("BTC-USD", "2h", start, end)
    with pytest.raises(ValueError, match="not natively fetchable"):
        adapter.fetch_ohlcv("BTC-USD", "4h", start, end)  # derived on read
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.fetch_ohlcv("BTC-USD", "15m", start.replace(tzinfo=None), end)
    with pytest.raises(ValueError, match="strictly before"):
        adapter.fetch_ohlcv("BTC-USD", "15m", end, start)
    assert fake.candles_requests == []  # all rejected before any fetch


def test_symbol_is_normalized_to_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    start, end = _window(0, 2)

    bars = adapter.fetch_ohlcv(" btc-usd ", "15m", start, end, now=_LONG_AFTER)

    assert all(b.symbol == "BTC-USD" for b in bars)
