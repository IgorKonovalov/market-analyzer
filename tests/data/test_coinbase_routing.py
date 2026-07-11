"""Plan 0081 phase 2 — N-source routing, per-source timeframe seam, provenance.

Phase-2 done-when claims pinned here:
(a) `BTC-USD`/`ETH-USD` route to Coinbase; `BTCUSDT` still routes to Binance;
    `AAPL`/`SPY` still route to Yahoo; a Coinbase symbol not in the product set
    falls to Yahoo — asserted via spies on all three sources;
(b) precedence Binance -> Coinbase -> Yahoo, asserted with a symbol placed in
    both exchange fixture sets (it routes to Binance);
(c) `4h`/`1w`/`1mo` for a Coinbase symbol derive from the Coinbase `1h`/`1d`
    base (never Yahoo), trailing and deterministic (byte-stable re-run);
(d) provenance: with a Yahoo-sourced `BTC-USD` row pre-seeded and Coinbase
    routed, `get_ohlcv` returns no Yahoo bars — the source-scoped cache read is
    the guard against the provenance switch leaking mixed bars.

All offline: membership comes from pre-written cache files, and the fake
transports never reach the network.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.binance_klines import BinanceKlinesAdapter, BinanceSpotHttpClient
from market_analyser.data.adapters.coinbase import CoinbaseAdapter, CoinbaseHttpClient
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.resample import resample_ohlcv as _real_resample
from market_analyser.data.types import Bar, SymbolInfo
from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory
from market_analyser.persistence.repository import BarRepository

_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_STEP_15M = 900
_SERVER_TIME_MS = int(datetime(2026, 6, 10, tzinfo=UTC).timestamp()) * 1000


# --- fakes: Coinbase transport ---------------------------------------------------


def _cb_candle(ts_sec: int, i: int) -> list[Any]:
    """A `[time, low, high, open, close, volume]` candle with the OHLC invariants."""
    return [ts_sec, 99.0 + i, 101.5 + i, 100.0 + i, 100.5 + i, 10.0 + i]


def _cb_series(anchor: datetime, step: int, count: int) -> list[list[Any]]:
    base = int(anchor.timestamp())
    return [_cb_candle(base + k * step, k) for k in range(count)]


class _FakeCoinbaseTransport:
    """Serves `/candles` (per-granularity fixture, windowed + newest-first +
    300-capped), `/ticker`, and `/products`. Records candle requests."""

    def __init__(
        self,
        *,
        products: list[dict[str, Any]] | None = None,
        candles_by_gran: dict[int, list[list[Any]]] | None = None,
    ) -> None:
        self._products = products if products is not None else []
        self._candles_by_gran = candles_by_gran if candles_by_gran is not None else {}
        self.candles_requests: list[dict[str, str]] = []
        self.products_requests = 0

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        split = urllib.parse.urlsplit(url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(split.query).items()}
        resp_headers: dict[str, str] = {}
        if "/candles" in split.path:
            self.candles_requests.append(query)
            gran = int(query["granularity"])
            start_sec = int(datetime.fromisoformat(query["start"]).timestamp())
            end_sec = int(datetime.fromisoformat(query["end"]).timestamp())
            page = [
                c for c in self._candles_by_gran.get(gran, []) if start_sec <= int(c[0]) <= end_sec
            ]
            page.sort(key=lambda c: c[0], reverse=True)
            payload: Any = page[:300]
        else:  # /products
            self.products_requests += 1
            payload = self._products
            resp_headers["Date"] = "Mon, 01 Jan 2024 00:00:00 GMT"
        return HttpResponse(
            status_code=200,
            headers=resp_headers,
            body=json.dumps(payload).encode(),
            elapsed_seconds=0.0,
        )


def _coinbase_adapter(
    monkeypatch: pytest.MonkeyPatch, *, transport: Any, cache_path: Path | None
) -> CoinbaseAdapter:
    client = CoinbaseHttpClient(source_name="coinbase-test", cache_ttl_seconds=0.0, max_retries=0)
    monkeypatch.setattr(client, "_perform_request", transport)
    return CoinbaseAdapter(http_client=client, symbol_cache_path=cache_path)


def _write_coinbase_cache(path: Path, symbols: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "coinbase",
                "symbols": sorted(symbols),
                "fetched_at": "2026-06-10T00:00:00+00:00",
            },
        ),
        encoding="utf-8",
    )


# --- fakes: Binance transport ----------------------------------------------------


def _binance_kline(anchor: datetime, hour_index: int) -> list[Any]:
    open_ms = int(anchor.timestamp() * 1000) + hour_index * 3_600_000
    return [
        open_ms,
        "100.0",
        "101.5",
        "99.0",
        "100.5",
        "10.0",
        open_ms + 3_599_999,
        "0",
        1,
        "0",
        "0",
        "0",
    ]


class _FakeBinanceTransport:
    def __init__(
        self, *, symbols: list[str] | None = None, klines: list[list[Any]] | None = None
    ) -> None:
        self._symbols = symbols if symbols is not None else []
        self._klines = klines if klines is not None else []
        self.klines_requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        query = {
            k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()
        }
        if "exchangeInfo" in url:
            payload: Any = {
                "timezone": "UTC",
                "serverTime": _SERVER_TIME_MS,
                "symbols": [{"symbol": s, "status": "TRADING"} for s in self._symbols],
            }
        else:
            self.klines_requests.append(query)
            start_ms, end_ms = int(query["startTime"]), int(query["endTime"])
            payload = [k for k in self._klines if start_ms <= int(k[0]) <= end_ms]
        return HttpResponse(
            status_code=200, headers={}, body=json.dumps(payload).encode(), elapsed_seconds=0.0
        )


def _binance_adapter(
    monkeypatch: pytest.MonkeyPatch, *, transport: Any, cache_path: Path
) -> BinanceKlinesAdapter:
    client = BinanceSpotHttpClient(source_name="binance-test", cache_ttl_seconds=0.0, max_retries=0)
    monkeypatch.setattr(client, "_perform_request", transport)
    return BinanceKlinesAdapter(http_client=client, symbol_cache_path=cache_path)


def _write_binance_cache(path: Path, symbols: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "binance",
                "symbols": sorted(symbols),
                "fetched_at": "2026-06-10T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


# --- fakes: Yahoo spy ------------------------------------------------------------


def _yahoo_spy(rows: list[dict[str, Any]]) -> tuple[YahooAdapter, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        calls.append((symbol, interval))
        return rows

    return YahooAdapter(fetcher=fetcher), calls


def _daily_yahoo_row(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000.0,
    }


# --- (a) membership routing across three sources ---------------------------------


@pytest.mark.parametrize("symbol", ["BTC-USD", "ETH-USD"])
def test_coinbase_pairs_route_to_coinbase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, symbol: str
) -> None:
    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, ["BTC-USD", "ETH-USD", "SOL-USD"])
    candles = _cb_series(_BASE, _STEP_15M, 3)
    cb = _coinbase_adapter(
        monkeypatch,
        transport=_FakeCoinbaseTransport(candles_by_gran={_STEP_15M: candles}),
        cache_path=cb_cache,
    )
    bn_cache = tmp_path / "binance_exchange_info.json"
    _write_binance_cache(bn_cache, ["BTCUSDT"])
    bn = _binance_adapter(monkeypatch, transport=_FakeBinanceTransport(), cache_path=bn_cache)
    yahoo, yahoo_calls = _yahoo_spy([])
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=bn, coinbase=cb)

    bars = provider.get_ohlcv(symbol, "15m", _BASE, _BASE + timedelta(minutes=45))

    assert len(bars) == 3
    assert all(b.source == "coinbase" for b in bars)
    assert all(b.symbol == symbol for b in bars)
    assert yahoo_calls == []  # Yahoo never consulted for a Coinbase member


def test_btcusdt_still_routes_to_binance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, ["BTC-USD"])
    cb = _coinbase_adapter(monkeypatch, transport=_FakeCoinbaseTransport(), cache_path=cb_cache)
    bn_cache = tmp_path / "binance_exchange_info.json"
    _write_binance_cache(bn_cache, ["BTCUSDT"])
    bn = _binance_adapter(
        monkeypatch,
        transport=_FakeBinanceTransport(klines=[_binance_kline(_BASE, i) for i in range(3)]),
        cache_path=bn_cache,
    )
    yahoo, yahoo_calls = _yahoo_spy([])
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=bn, coinbase=cb)

    bars = provider.get_ohlcv("BTCUSDT", "1h", _BASE, _BASE + timedelta(hours=3))

    assert len(bars) == 3
    assert all(b.source == "binance" for b in bars)
    assert yahoo_calls == []


@pytest.mark.parametrize("symbol", ["AAPL", "SPY", "DOGE-USD"])
def test_non_coinbase_symbols_fall_to_yahoo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, symbol: str
) -> None:
    """Equities/indices and a crypto -USD pair Coinbase does NOT list all route
    to Yahoo (DOGE-USD is absent from the fixture product set)."""
    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, ["BTC-USD", "ETH-USD"])
    cb = _coinbase_adapter(monkeypatch, transport=_FakeCoinbaseTransport(), cache_path=cb_cache)
    bn_cache = tmp_path / "binance_exchange_info.json"
    _write_binance_cache(bn_cache, ["BTCUSDT"])
    bn = _binance_adapter(monkeypatch, transport=_FakeBinanceTransport(), cache_path=bn_cache)
    yahoo, yahoo_calls = _yahoo_spy([_daily_yahoo_row("2024-01-02")])
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=bn, coinbase=cb)

    bars = provider.get_ohlcv(symbol, "1d", _BASE, _BASE + timedelta(days=5))

    assert [b.source for b in bars] == ["yahoo"]
    assert yahoo_calls == [(symbol, "1d")]


# --- (b) precedence Binance -> Coinbase -> Yahoo ---------------------------------


def test_precedence_binance_wins_over_coinbase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A symbol present in BOTH exchange sets routes to Binance (the precedence
    guard — no real symbol collides, but the order is asserted)."""
    collision = "XYZ"
    bn_cache = tmp_path / "binance_exchange_info.json"
    _write_binance_cache(bn_cache, [collision])
    bn = _binance_adapter(
        monkeypatch,
        transport=_FakeBinanceTransport(klines=[_binance_kline(_BASE, i) for i in range(2)]),
        cache_path=bn_cache,
    )
    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, [collision])
    cb = _coinbase_adapter(
        monkeypatch,
        transport=_FakeCoinbaseTransport(candles_by_gran={3600: _cb_series(_BASE, 3600, 2)}),
        cache_path=cb_cache,
    )
    yahoo, yahoo_calls = _yahoo_spy([])
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=bn, coinbase=cb)

    bars = provider.get_ohlcv(collision, "1h", _BASE, _BASE + timedelta(hours=2))

    assert all(b.source == "binance" for b in bars)  # Binance wins
    assert cb.is_known_symbol(collision)  # it IS in the Coinbase set too
    assert yahoo_calls == []


# --- (c) derived timeframes come from the Coinbase base, deterministically -------


@pytest.mark.parametrize(
    ("timeframe", "base_gran", "step", "count"),
    [("4h", 3600, 3600, 48), ("1w", 86400, 86400, 40), ("1mo", 86400, 86400, 40)],
)
def test_coarse_timeframes_derive_from_coinbase_base_not_yahoo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeframe: str,
    base_gran: int,
    step: int,
    count: int,
) -> None:
    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, ["BTC-USD"])
    series = _cb_series(_BASE, step, count)
    cb = _coinbase_adapter(
        monkeypatch,
        transport=_FakeCoinbaseTransport(candles_by_gran={base_gran: series}),
        cache_path=cb_cache,
    )
    yahoo, yahoo_calls = _yahoo_spy([])
    provider = DefaultMarketDataProvider(yahoo=yahoo, coinbase=cb)

    resample_calls: list[str] = []

    def resample_spy(bars: list[Any], target: str) -> list[Any]:
        resample_calls.append(target)
        return _real_resample(bars, target=target)

    monkeypatch.setattr("market_analyser.data.default_provider.resample_ohlcv", resample_spy)

    end = _BASE + timedelta(seconds=step * (count - 1))
    bars = provider.get_ohlcv("BTC-USD", timeframe, _BASE, end)

    assert resample_calls == [timeframe]  # derived on read (ADR-0028)
    assert bars, "derivation produced bars"
    assert all(b.timeframe == timeframe for b in bars)
    assert all(b.source == "coinbase" for b in bars)  # base fetched from Coinbase, never Yahoo
    assert yahoo_calls == []

    # Deterministic: a byte-stable re-run (same inputs -> same output).
    rerun = provider.get_ohlcv("BTC-USD", timeframe, _BASE, end)
    assert [b.model_dump() for b in rerun] == [b.model_dump() for b in bars]


# --- (d) provenance-scoped cache read --------------------------------------------


def test_source_scoped_read_excludes_orphaned_yahoo_bars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Yahoo-sourced BTC-USD row pre-seeded in the cache is never returned once
    BTC-USD routes to Coinbase — the source-scoped read is the guard against the
    provenance switch leaking mixed bars (ADR-0076). The Coinbase fetch fills the
    window with its own bars; the physically-present Yahoo rows stay behind the
    filter."""
    engine = make_engine(":memory:")
    apply_migrations(engine)
    repo = BarRepository(make_session_factory(engine))
    # Pre-seed two Yahoo-sourced BTC-USD 15m bars in the window.
    yahoo_ts = [_BASE, _BASE + timedelta(minutes=15)]
    repo.upsert_bars(
        [
            Bar(
                symbol="BTC-USD",
                timeframe="15m",
                event_ts=ts,
                open=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
                volume=5.0,
                source="yahoo",
            )
            for ts in yahoo_ts
        ]
    )

    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, ["BTC-USD"])
    # Coinbase serves candles at LATER timestamps (T2..T4), distinct from the
    # seeded Yahoo rows, so a leak would be visible as a Yahoo-sourced bar.
    cb_candles = _cb_series(_BASE + timedelta(minutes=30), _STEP_15M, 3)
    cb = _coinbase_adapter(
        monkeypatch,
        transport=_FakeCoinbaseTransport(candles_by_gran={_STEP_15M: cb_candles}),
        cache_path=cb_cache,
    )
    yahoo, _ = _yahoo_spy([])
    provider = DefaultMarketDataProvider(yahoo=yahoo, coinbase=cb, bar_repository=repo)

    end = _BASE + timedelta(minutes=60)
    bars = provider.get_ohlcv("BTC-USD", "15m", _BASE, end)

    # No Yahoo bars leak through; every returned bar is Coinbase-sourced.
    assert bars, "coinbase filled the window"
    assert all(b.source == "coinbase" for b in bars)
    assert all(b.event_ts not in yahoo_ts for b in bars)
    # The Yahoo rows are still physically present — an unscoped read sees them,
    # proving the exclusion was the source filter, not a delete.
    unscoped = repo.get_bars("BTC-USD", "15m", _BASE, end)
    assert any(b.source == "yahoo" for b in unscoped)

    engine.dispose()


# --- (e) search label reflects the routed source ---------------------------------


def test_search_relabels_dual_listed_symbol_to_its_routed_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A suggestion's source label must match where get_ohlcv routes it
    (ADR-0026 searchable==fetchable; ADR-0076). A dual-listed `BTC-USD` (returned
    by Yahoo AND listed by Coinbase) surfaces as Coinbase with Yahoo's nicer name
    kept; a Yahoo-only symbol is untouched; a symbol in both exchange sets takes
    Binance (precedence); exchange-only symbols append with their own labels."""
    yahoo, _ = _yahoo_spy([])
    # Yahoo's raw search: BTC-USD (its crypto composite, exch 'CCC'), a
    # Yahoo-only future, and 'BTCX' which both exchanges also list.
    monkeypatch.setattr(
        yahoo,
        "search",
        lambda query: [
            SymbolInfo(
                symbol="BTC-USD", name="Bitcoin USD", exchange="CCC", quote_type="Cryptocurrency"
            ),
            SymbolInfo(
                symbol="BTC=F", name="Bitcoin Futures", exchange="CME", quote_type="Futures"
            ),
            SymbolInfo(symbol="BTCX", name="Bitcoin X", exchange="NYS", quote_type="Equity"),
        ],
    )
    cb_cache = tmp_path / "coinbase_products.json"
    _write_coinbase_cache(cb_cache, ["BTC-USD", "BTC-USDC", "BTCX"])
    cb = _coinbase_adapter(monkeypatch, transport=_FakeCoinbaseTransport(), cache_path=cb_cache)
    bn_cache = tmp_path / "binance_exchange_info.json"
    _write_binance_cache(bn_cache, ["BTCUSDT", "BTCX"])
    bn = _binance_adapter(monkeypatch, transport=_FakeBinanceTransport(), cache_path=bn_cache)
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=bn, coinbase=cb)

    results = provider.search_symbols("BTC")
    by_symbol = {r.symbol: r for r in results}

    # Dual-listed: relabeled to Coinbase (its routed source), Yahoo name kept.
    assert by_symbol["BTC-USD"].exchange == "Coinbase"
    assert by_symbol["BTC-USD"].name == "Bitcoin USD"
    # Yahoo-only future: label untouched.
    assert by_symbol["BTC=F"].exchange == "CME"
    # In BOTH exchange sets -> Binance wins precedence (mirrors _ohlcv_route).
    assert by_symbol["BTCX"].exchange == "Binance"
    # Exchange-only symbols Yahoo didn't return append with their own labels.
    assert by_symbol["BTCUSDT"].exchange == "Binance"
    assert by_symbol["BTC-USDC"].exchange == "Coinbase"
    # The relabel matches what OHLCV routing would pick, for each symbol.
    assert provider._ohlcv_route("BTC-USD")[0] == "coinbase"
    assert provider._ohlcv_route("BTCX")[0] == "binance"
