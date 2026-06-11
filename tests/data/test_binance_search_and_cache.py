"""Plan 0058 phase 3 — Binance symbol search + bars-cache coexistence.

Phase-3 done-when claims pinned here:
(a) searching "BTCUSDT" returns the Binance pair with its source labeled
    (`exchange="Binance"`), merged after Yahoo's results;
(b) the cache round-trip (fetch → cache → re-read without network,
    spy-asserted) passes for a Binance-routed symbol — at 1h and at the
    natively-cached 4h;
(c) the strictly-historical-empty vs leading-edge-empty split (ADR-0033) is
    asserted for the Binance path through the provider.

Plus the search contract on the adapter itself: deterministic ranking (exact,
then prefix, then substring — alphabetical within each group), the result cap,
the empty/zero-match short-circuits, and no network when the symbol cache file
is present.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.binance_klines import (
    BinanceKlinesAdapter,
    BinanceSpotHttpClient,
)
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import UnknownSymbolError
from market_analyser.data.sources import SymbolSearchSource
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repository import BarRepository

# 2024-01-01T00:00:00Z — hour-aligned fixture anchor.
_BASE = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture
def repo() -> Iterator[BarRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BarRepository(make_session_factory(engine))
    engine.dispose()


def _kline(anchor: datetime, hour_index: int) -> list[Any]:
    """One hourly kline array in the documented 12-element wire shape."""
    open_ms = int(anchor.timestamp() * 1000) + hour_index * 3_600_000
    return [
        open_ms,
        "100.00000000",
        "101.50000000",
        "99.00000000",
        "100.50000000",
        "10.00000000",
        open_ms + 3_599_999,
        "2434.19055334",
        308,
        "1756.87402397",
        "28.46694368",
        "0",
    ]


class _FakeKlinesTransport:
    """Transport seam serving `/api/v3/klines` from a fixed kline list,
    filtered to `[startTime, endTime]` by open time (single page). Counts
    every klines request so the no-refetch claim is spy-asserted."""

    def __init__(self, klines: list[list[Any]]) -> None:
        self._klines = klines
        self.klines_requests: list[str] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        import urllib.parse

        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        assert "klines" in url, f"unexpected non-klines request: {url}"
        self.klines_requests.append(query.get("interval", ""))
        start_ms = int(query["startTime"])
        end_ms = int(query["endTime"])
        payload = [k for k in self._klines if start_ms <= int(k[0]) <= end_ms]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(payload).encode("utf-8"),
            elapsed_seconds=0.0,
        )


def _failing_transport() -> Any:
    """A transport that fails the test if any request reaches it."""

    class _Failing:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(self, *args: Any, **kwargs: Any) -> HttpResponse:
            self.attempts += 1
            raise AssertionError("no network call expected")

    return _Failing()


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


def _binance_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    symbols: list[str],
    transport: Any | None = None,
) -> tuple[BinanceKlinesAdapter, Any]:
    cache = tmp_path / "binance_exchange_info.json"
    _write_cache(cache, symbols)
    client = BinanceSpotHttpClient(source_name="binance-test", cache_ttl_seconds=0.0, max_retries=0)
    fake = transport if transport is not None else _failing_transport()
    monkeypatch.setattr(client, "_perform_request", fake)
    return BinanceKlinesAdapter(http_client=client, symbol_cache_path=cache), fake


def _patch_yahoo_search(monkeypatch: pytest.MonkeyPatch, quotes: list[dict[str, Any]]) -> list[str]:
    """Replace the Yahoo search fetcher; returns the recorded query log."""
    queries: list[str] = []

    def fake_search(query: str, *, client: Any, quotes_count: int) -> list[dict[str, Any]]:
        queries.append(query)
        return quotes

    monkeypatch.setattr("market_analyser.data.adapters.yahoo._fetch_yahoo_search", fake_search)
    return queries


_YAHOO_BTC_QUOTE = {
    "symbol": "BTC-USD",
    "longname": "Bitcoin USD",
    "exchDisp": "CCC",
    "typeDisp": "Cryptocurrency",
}


# --- (a) search: the Binance pair surfaces, source-labeled, after Yahoo ----------


def test_adapter_satisfies_symbol_search_source_protocol() -> None:
    assert isinstance(BinanceKlinesAdapter(), SymbolSearchSource)


def test_search_btcusdt_returns_the_labeled_binance_pair_after_yahoo_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binance, _ = _binance_adapter(monkeypatch, tmp_path, symbols=["BTCUSDT", "ETHUSDT"])
    _patch_yahoo_search(monkeypatch, [_YAHOO_BTC_QUOTE])
    provider = DefaultMarketDataProvider(yahoo=YahooAdapter(), binance=binance)

    results = provider.search_symbols("BTCUSDT")

    assert [r.symbol for r in results] == ["BTC-USD", "BTCUSDT"]  # Yahoo first
    binance_hit = results[1]
    assert binance_hit.exchange == "Binance"  # the source label (ADR-0052)
    assert binance_hit.quote_type == "Cryptocurrency"
    assert binance_hit.name == "BTCUSDT"
    yahoo_hit = results[0]
    assert yahoo_hit.exchange == "CCC"  # Yahoo's own labeling untouched


def test_search_merge_skips_a_symbol_string_yahoo_already_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If Yahoo ever returns the literal same symbol string, the merged list
    carries it once — membership routing decides the fetch source either way,
    so a duplicate row would be pure picker noise."""
    binance, _ = _binance_adapter(monkeypatch, tmp_path, symbols=["BTCUSDT"])
    _patch_yahoo_search(monkeypatch, [{"symbol": "BTCUSDT", "shortname": "BTC Tether"}])
    provider = DefaultMarketDataProvider(yahoo=YahooAdapter(), binance=binance)

    results = provider.search_symbols("BTCUSDT")

    assert [r.symbol for r in results] == ["BTCUSDT"]


def test_search_without_a_wired_binance_adapter_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_yahoo_search(monkeypatch, [_YAHOO_BTC_QUOTE])
    provider = DefaultMarketDataProvider(yahoo=YahooAdapter())

    results = provider.search_symbols("BTCUSDT")

    assert [r.symbol for r in results] == ["BTC-USD"]


def test_adapter_search_ranks_exact_then_prefix_then_substring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter, fake = _binance_adapter(
        monkeypatch,
        tmp_path,
        symbols=["XBTCUSDT", "BTCUSDT", "BTCUSDC", "ETHUSDT", "BTCDOWNUSDT"],
    )

    results = adapter.search("btcusd")  # case-insensitive

    assert [r.symbol for r in results] == ["BTCUSDC", "BTCUSDT", "XBTCUSDT"]

    exact = adapter.search("BTCUSDT")
    assert [r.symbol for r in exact] == ["BTCUSDT", "XBTCUSDT"]  # exact first
    assert fake.attempts == 0  # the cache file answered everything


def test_adapter_search_caps_results_and_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    many = [f"AAA{i:02d}USDT" for i in range(15)]
    adapter, _ = _binance_adapter(monkeypatch, tmp_path, symbols=many)

    assert len(adapter.search("USDT")) == 10  # capped
    assert adapter.search("   ") == []  # empty query: no work
    assert adapter.search("ZZZZZZ") == []  # zero matches: not an error


# --- (b) cache round-trip: fetch → cache → re-read without network ---------------


def test_binance_1h_cache_round_trip_refetches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: BarRepository
) -> None:
    transport = _FakeKlinesTransport([_kline(_BASE, i) for i in range(4)])
    binance, _ = _binance_adapter(monkeypatch, tmp_path, symbols=["BTCUSDT"], transport=transport)
    provider = DefaultMarketDataProvider(binance=binance, bar_repository=repo)
    start, end = _BASE, _BASE + timedelta(hours=3)

    first = provider.get_ohlcv("BTCUSDT", "1h", start, end)
    fetches_after_first = len(transport.klines_requests)
    second = provider.get_ohlcv("BTCUSDT", "1h", start, end)

    assert len(first) == 4
    assert all(b.source == "binance" for b in first)
    assert fetches_after_first >= 1  # the first read really fetched
    assert len(transport.klines_requests) == fetches_after_first  # no refetch
    assert list(second) == list(first)  # the cache served identical bars


def test_binance_4h_is_cached_natively_and_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: BarRepository
) -> None:
    """4h bars for a Binance member are fetched AND cached at 4h (no base
    redirect): coverage reports the 4h cache itself as complete, and a re-read
    is network-free."""
    transport = _FakeKlinesTransport([_kline(_BASE, 4 * i) for i in range(3)])
    binance, _ = _binance_adapter(monkeypatch, tmp_path, symbols=["BTCUSDT"], transport=transport)
    provider = DefaultMarketDataProvider(binance=binance, bar_repository=repo)
    start, end = _BASE, _BASE + timedelta(hours=8)

    first = provider.get_ohlcv("BTCUSDT", "4h", start, end)
    fetches_after_first = len(transport.klines_requests)
    second = provider.get_ohlcv("BTCUSDT", "4h", start, end)

    assert [b.timeframe for b in first] == ["4h", "4h", "4h"]
    assert all(interval == "4h" for interval in transport.klines_requests)
    assert len(transport.klines_requests) == fetches_after_first  # no refetch
    assert list(second) == list(first)

    coverage = provider.coverage("BTCUSDT", "4h", start, end)
    assert coverage.gaps == []  # the 4h cache is its own coverage (no 1h base)
    assert [b.timeframe for b in coverage.cached] == ["4h", "4h", "4h"]


# --- (c) ADR-0033: empty-window split for the Binance path through the provider --


def test_binance_leading_edge_empty_raises_unknown_symbol_through_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: BarRepository
) -> None:
    transport = _FakeKlinesTransport([])  # upstream has nothing for this pair
    binance, _ = _binance_adapter(monkeypatch, tmp_path, symbols=["GONEUSDT"], transport=transport)
    provider = DefaultMarketDataProvider(binance=binance, bar_repository=repo)
    frozen_now = _BASE + timedelta(days=10)
    monkeypatch.setattr("market_analyser.data.default_provider._now", lambda: frozen_now)

    with pytest.raises(UnknownSymbolError) as excinfo:
        # The window reaches the leading edge (`end` == now): a live, listed
        # pair must have data there, so emptiness is an unknown symbol.
        provider.get_ohlcv("GONEUSDT", "1h", frozen_now - timedelta(days=2), frozen_now)

    assert excinfo.value.symbol == "GONEUSDT"


def test_binance_strictly_historical_empty_returns_empty_through_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: BarRepository
) -> None:
    """A pair listed after the requested window has no bars there — a
    legitimate end-of-history (ADR-0033): `[]`, never an unknown-symbol error,
    exactly as the Yahoo path behaves."""
    transport = _FakeKlinesTransport([])
    binance, _ = _binance_adapter(monkeypatch, tmp_path, symbols=["BTCUSDT"], transport=transport)
    provider = DefaultMarketDataProvider(binance=binance, bar_repository=repo)
    frozen_now = _BASE + timedelta(days=10)
    monkeypatch.setattr("market_analyser.data.default_provider._now", lambda: frozen_now)

    bars = provider.get_ohlcv(
        "BTCUSDT",
        "1h",
        _BASE - timedelta(days=2),
        _BASE,  # ends 10 days before now
    )

    assert list(bars) == []
