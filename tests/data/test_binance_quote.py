"""Plan 0058 follow-up — Binance live quote (`QuoteSource` via /api/v3/ticker/24hr).

The klines/search/routing halves of Plan 0058 made `get_ohlcv` and
`search_symbols` Binance-aware, but `get_quote` still always hit Yahoo, which
404s on a Binance-only pair — so the viewer's live price header showed
"disconnected" for `BTCUSDT`. This pins the follow-up:

(a) `BinanceKlinesAdapter` satisfies `QuoteSource` and maps the 24hr payload onto
    `Quote` — `lastPrice`→price, `priceChangePercent`→`change_pct` UNSCALED (incl.
    negative), `closeTime`→`as_of`, day range / prev close / volume, currency
    "USDT" only when the pair name makes it unambiguous;
(b) the typed-error taxonomy mirrors `fetch_ohlcv` (451→`GeoRestrictedError` and
    never retried, 429→`RateLimitedError` with `Retry-After`, 5xx→
    `UpstreamUnavailableError`), and shape drift → `BinanceKlinesError`;
(c) `DefaultMarketDataProvider.get_quote` routes by exchangeInfo membership —
    `BTCUSDT`→Binance, `AAPL`/`BTC-USD`→Yahoo, unwired-binance→Yahoo.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.binance_klines import (
    BinanceKlinesAdapter,
    BinanceKlinesError,
    BinanceSpotHttpClient,
)
from market_analyser.data.adapters.yahoo_quote import YahooQuoteAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from market_analyser.data.sources import QuoteSource
from market_analyser.data.types import Quote

# `closeTime` is epoch ms; the mapping turns it into the quote's `as_of`.
_CLOSE_TIME_MS = 1_749_772_800_000


def _ticker(
    symbol: str = "BTCUSDT", *, last: str = "63600.01", change_pct: str = "2.51"
) -> dict[str, Any]:
    """A representative /api/v3/ticker/24hr payload (documented shape; prices are
    the upstream's decimal-string wire encoding)."""
    return {
        "symbol": symbol,
        "lastPrice": last,
        "priceChangePercent": change_pct,
        "prevClosePrice": "62042.00",
        "highPrice": "64100.00",
        "lowPrice": "61500.00",
        "openPrice": "62050.00",
        "volume": "12345.678",
        "closeTime": _CLOSE_TIME_MS,
    }


class _FakeTicker:
    """Transport seam returning a fixed 24hr payload; records the queried symbol
    and asserts the request actually went to the ticker endpoint."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.requests: list[str] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        assert "ticker/24hr" in url, f"unexpected non-ticker request: {url}"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.requests.append(query.get("symbol", [""])[0])
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(self._payload).encode("utf-8"),
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
                status_code=status_code, headers=headers or {}, body=body, elapsed_seconds=0.0
            )

    return _Static()


def _quote_adapter(
    monkeypatch: pytest.MonkeyPatch,
    transport: Any,
    *,
    max_retries: int = 0,
    cache_path: Path | None = None,
) -> BinanceKlinesAdapter:
    client = BinanceSpotHttpClient(
        source_name="binance-test", cache_ttl_seconds=0.0, max_retries=max_retries
    )
    monkeypatch.setattr(client, "_perform_request", transport)
    return BinanceKlinesAdapter(http_client=client, symbol_cache_path=cache_path)


def _write_cache(path: Path, symbols: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "binance",
                "symbols": sorted(symbols),
                "fetched_at": "2026-06-10T00:00:00+00:00",
            },
        ),
        encoding="utf-8",
    )


def _yahoo_quote_spy() -> tuple[YahooQuoteAdapter, list[str]]:
    """A real `YahooQuoteAdapter` (so the provider's type is satisfied) whose
    fetcher records each queried symbol and returns a minimal meta block."""
    calls: list[str] = []

    def fetcher(symbol: str) -> dict[str, Any]:
        calls.append(symbol)
        return {"regularMarketPrice": 195.0, "currency": "USD", "regularMarketTime": 1_749_772_800}

    return YahooQuoteAdapter(fetcher=fetcher), calls


# --- (a) adapter satisfies QuoteSource and maps the 24hr payload -----------------


def test_adapter_satisfies_quote_source_protocol() -> None:
    assert isinstance(BinanceKlinesAdapter(), QuoteSource)


def test_get_quote_maps_ticker_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeTicker(_ticker())
    adapter = _quote_adapter(monkeypatch, fake)

    q = adapter.get_quote(" btcusdt ")  # normalized to upper, stripped

    assert isinstance(q, Quote)
    assert q.symbol == "BTCUSDT"
    assert q.price == 63600.01
    assert q.change_pct == 2.51  # already a percent — NOT multiplied by 100
    assert q.previous_close == 62042.00
    assert q.day_high == 64100.00
    assert q.day_low == 61500.00
    assert q.volume == 12345.678
    assert q.currency == "USDT"
    assert q.market_state == ""  # crypto has no Yahoo-style session phase
    assert q.source == "binance"
    assert q.as_of == datetime.fromtimestamp(_CLOSE_TIME_MS / 1000, tz=UTC)
    assert fake.requests == ["BTCUSDT"]


def test_negative_change_pct_is_carried_unscaled(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _quote_adapter(monkeypatch, _FakeTicker(_ticker(change_pct="-3.14")))
    assert adapter.get_quote("BTCUSDT").change_pct == -3.14


def test_currency_is_blank_for_a_non_usdt_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ticker payload carries no quote asset, so a non-USDT pair is left ""
    rather than mislabelled — only an unambiguous `*USDT` name sets "USDT"."""
    adapter = _quote_adapter(monkeypatch, _FakeTicker(_ticker(symbol="ETHBTC")))
    assert adapter.get_quote("ETHBTC").currency == ""


def test_empty_symbol_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _quote_adapter(monkeypatch, _FakeTicker(_ticker()))
    with pytest.raises(ValueError, match="non-empty"):
        adapter.get_quote("   ")


# --- (b) typed-error taxonomy mirrors fetch_ohlcv --------------------------------


def test_451_raises_geo_restricted_not_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # max_retries=3: retries are available, so one attempt proves 451 is permanent.
    client = BinanceSpotHttpClient(source_name="binance-test", cache_ttl_seconds=0.0, max_retries=3)
    fake = _static_response(451, b'{"code": 0, "msg": "restricted location"}')
    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = BinanceKlinesAdapter(http_client=client)

    with pytest.raises(GeoRestrictedError, match="451"):
        adapter.get_quote("BTCUSDT")
    assert fake.attempts == 1
    assert client.stats().retries == 0


def test_429_maps_to_rate_limited_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _quote_adapter(monkeypatch, _static_response(429, b"{}", {"Retry-After": "30"}))
    with pytest.raises(RateLimitedError) as excinfo:
        adapter.get_quote("BTCUSDT")
    assert excinfo.value.retry_after_seconds == 30


def test_5xx_maps_to_upstream_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _quote_adapter(monkeypatch, _static_response(500, b""))
    with pytest.raises(UpstreamUnavailableError, match="HTTP 500"):
        adapter.get_quote("BTCUSDT")


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ('{"priceChangePercent": "1", "closeTime": 1}', "lastPrice"),  # no lastPrice
        ('{"lastPrice": "0", "priceChangePercent": "1", "closeTime": 1}', "non-positive"),
        ('{"lastPrice": "100", "priceChangePercent": "x", "closeTime": 1}', "priceChangePercent"),
        ('{"lastPrice": "100", "priceChangePercent": "1"}', "closeTime"),  # no closeTime
        ("[1, 2, 3]", "not an object"),
    ],
)
def test_shape_drift_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, payload: str, match: str
) -> None:
    adapter = _quote_adapter(monkeypatch, _static_response(200, payload.encode("utf-8")))
    with pytest.raises(BinanceKlinesError, match=match):
        adapter.get_quote("BTCUSDT")


# --- (c) provider routes quotes by exchangeInfo membership -----------------------


def test_btcusdt_quote_routes_to_binance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "binance_exchange_info.json"
    _write_cache(cache, ["BTCUSDT", "ETHUSDT"])
    binance = _quote_adapter(monkeypatch, _FakeTicker(_ticker()), cache_path=cache)
    yahoo_quote, yahoo_calls = _yahoo_quote_spy()
    provider = DefaultMarketDataProvider(yahoo_quote=yahoo_quote, binance=binance)

    q = provider.get_quote("BTCUSDT")

    assert q.source == "binance"
    assert q.price == 63600.01
    assert yahoo_calls == []  # Yahoo never consulted for a Binance member


@pytest.mark.parametrize("symbol", ["AAPL", "BTC-USD"])
def test_non_member_quote_routes_to_yahoo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, symbol: str
) -> None:
    cache = tmp_path / "binance_exchange_info.json"
    _write_cache(cache, ["BTCUSDT"])
    # A failing Binance transport that must never be hit for a non-member.
    binance = _quote_adapter(monkeypatch, _static_response(500, b""), cache_path=cache)
    yahoo_quote, yahoo_calls = _yahoo_quote_spy()
    provider = DefaultMarketDataProvider(yahoo_quote=yahoo_quote, binance=binance)

    q = provider.get_quote(symbol)

    assert q.source == "yahoo"
    assert yahoo_calls == [symbol]


def test_unwired_binance_quotes_via_yahoo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider without a Binance adapter (every offline fixture today) quotes
    everything through Yahoo — BTCUSDT included — exactly as before the follow-up."""
    yahoo_quote, yahoo_calls = _yahoo_quote_spy()
    provider = DefaultMarketDataProvider(yahoo_quote=yahoo_quote)

    provider.get_quote("BTCUSDT")

    assert yahoo_calls == ["BTCUSDT"]
