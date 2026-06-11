"""Plan 0058 phase 2 — exchangeInfo membership + provider dispatch.

Phase-2 done-when claims pinned here:
(a) `BTCUSDT` routes to the Binance adapter while `AAPL` and `BTC-USD` still
    route to Yahoo — asserted via spy adapters on both sides;
(b) a symbol in neither universe fails with the existing unknown-symbol
    taxonomy (`UnknownSymbolError`, the 404 path unchanged);
(c) a 1h request older than 730 days **succeeds** for `BTCUSDT` and still
    clamps (`HistoryExceededError`) for `BTC-USD` — the cap is per-source,
    asserted both ways;
(d) `4h` for `BTCUSDT` is served native (a spy asserts no resample call and
    the wire carries `interval=4h`) while Yahoo's 4h resample path is
    untouched.

Plus the membership-cache contract on the adapter itself: a present cache file
is used as-is with no network (stale-but-present beats absent); an absent file
triggers one lazy fetch-and-persist; a failed lazy fetch memoizes an empty set
for the process (everything routes to Yahoo — loud, never wedged) until an
explicit `refresh_symbols()`; a corrupt file is treated as absent.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.binance_klines import (
    BinanceKlinesAdapter,
    BinanceKlinesError,
    BinanceSpotHttpClient,
)
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import (
    GeoRestrictedError,
    HistoryExceededError,
    UnknownSymbolError,
)
from market_analyser.data.resample import resample_ohlcv as _real_resample
from market_analyser.data.timeframes import source_max_history, source_resampled_from

# 2024-01-01T00:00:00Z — hour-aligned fixture anchor.
_BASE = datetime(2024, 1, 1, tzinfo=UTC)
# Upstream's own clock for the exchangeInfo fixture (serverTime, epoch ms).
_SERVER_TIME_MS = int(datetime(2026, 6, 10, tzinfo=UTC).timestamp()) * 1000


def _exchange_info_body(symbols: list[str]) -> dict[str, Any]:
    """The documented /api/v3/exchangeInfo shape, at fixture scale."""
    return {
        "timezone": "UTC",
        "serverTime": _SERVER_TIME_MS,
        "rateLimits": [],
        "exchangeFilters": [],
        "symbols": [{"symbol": s, "status": "TRADING"} for s in symbols],
    }


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


class _FakeBinanceTransport:
    """Transport seam serving both spot endpoints: `/api/v3/exchangeInfo`
    returns the configured symbol universe; `/api/v3/klines` serves the
    configured klines filtered to `[startTime, endTime]` by open time (single
    page — the pagination walk itself is pinned by the phase-1 specs)."""

    def __init__(
        self,
        *,
        symbols: list[str] | None = None,
        klines: list[list[Any]] | None = None,
    ) -> None:
        self._symbols = symbols if symbols is not None else []
        self._klines = klines if klines is not None else []
        self.exchange_info_requests = 0
        self.klines_requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        if "exchangeInfo" in url:
            self.exchange_info_requests += 1
            payload: Any = _exchange_info_body(self._symbols)
        else:
            self.klines_requests.append(query)
            start_ms = int(query["startTime"])
            end_ms = int(query["endTime"])
            payload = [k for k in self._klines if start_ms <= int(k[0]) <= end_ms]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(payload).encode("utf-8"),
            elapsed_seconds=0.0,
        )


def _static_response(status_code: int, body: bytes) -> Any:
    """A transport fake that always returns one response, counting attempts."""

    class _Static:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(
            self, method: str, url: str, req_body: Any, req_headers: Any, *, proxy: Any
        ) -> HttpResponse:
            self.attempts += 1
            return HttpResponse(status_code=status_code, headers={}, body=body, elapsed_seconds=0.0)

    return _Static()


def _binance_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any,
    cache_path: Path | None = None,
) -> BinanceKlinesAdapter:
    client = BinanceSpotHttpClient(source_name="binance-test", cache_ttl_seconds=0.0, max_retries=0)
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


def _yahoo_spy(rows: list[dict[str, Any]]) -> tuple[YahooAdapter, list[tuple[str, str]]]:
    """A YahooAdapter whose fetcher records (symbol, interval) per call."""
    calls: list[tuple[str, str]] = []

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        calls.append((symbol, interval))
        return rows

    return YahooAdapter(fetcher=fetcher), calls


def _hourly_yahoo_rows(anchor: datetime, count: int) -> list[dict[str, Any]]:
    return [
        {
            "date": (anchor + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"),
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1_000.0,
        }
        for i in range(count)
    ]


def _provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    yahoo_rows: list[dict[str, Any]] | None = None,
    binance_symbols: list[str] | None = None,
    binance_klines: list[list[Any]] | None = None,
) -> tuple[DefaultMarketDataProvider, list[tuple[str, str]], _FakeBinanceTransport]:
    """A repo-less provider with spies on both OHLCV sources. The Binance
    symbol set comes from a pre-written cache file, so membership checks are
    file-only — the transport records any (unexpected) exchangeInfo call."""
    cache = tmp_path / "binance_exchange_info.json"
    _write_cache(cache, binance_symbols if binance_symbols is not None else ["BTCUSDT", "ETHUSDT"])
    transport = _FakeBinanceTransport(klines=binance_klines if binance_klines is not None else [])
    binance = _binance_adapter(monkeypatch, transport=transport, cache_path=cache)
    yahoo, yahoo_calls = _yahoo_spy(yahoo_rows if yahoo_rows is not None else [])
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=binance)
    return provider, yahoo_calls, transport


# --- (a) membership routing: BTCUSDT → Binance, AAPL / BTC-USD → Yahoo -----------


def test_btcusdt_routes_to_binance_and_yahoo_is_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, yahoo_calls, transport = _provider(
        monkeypatch, tmp_path, binance_klines=[_kline(_BASE, i) for i in range(3)]
    )

    bars = provider.get_ohlcv("BTCUSDT", "1h", _BASE, _BASE + timedelta(hours=3))

    assert len(bars) == 3
    assert all(b.source == "binance" for b in bars)
    assert all(b.symbol == "BTCUSDT" for b in bars)
    assert yahoo_calls == []  # Yahoo never consulted for a Binance member
    assert all(req["symbol"] == "BTCUSDT" for req in transport.klines_requests)


@pytest.mark.parametrize("symbol", ["AAPL", "BTC-USD"])
def test_non_members_still_route_to_yahoo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, symbol: str
) -> None:
    provider, yahoo_calls, transport = _provider(
        monkeypatch,
        tmp_path,
        yahoo_rows=[
            {
                "date": "2024-01-02",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000.0,
            },
        ],
    )

    bars = provider.get_ohlcv(symbol, "1d", _BASE, _BASE + timedelta(days=5))

    assert [b.source for b in bars] == ["yahoo"]
    assert yahoo_calls == [(symbol, "1d")]
    assert transport.klines_requests == []  # Binance never consulted
    assert transport.exchange_info_requests == 0  # the cache file answered membership


def test_unwired_binance_keeps_pre_plan_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider without a Binance adapter (every offline test fixture today)
    routes everything to Yahoo — BTCUSDT included."""
    yahoo, yahoo_calls = _yahoo_spy(
        [
            {
                "date": "2024-01-02",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000.0,
            },
        ],
    )
    provider = DefaultMarketDataProvider(yahoo=yahoo)

    provider.get_ohlcv("BTCUSDT", "1d", _BASE, _BASE + timedelta(days=5))

    assert yahoo_calls == [("BTCUSDT", "1d")]


# --- (b) a symbol in neither universe: unknown-symbol taxonomy unchanged ---------


def test_symbol_in_neither_universe_fails_with_unknown_symbol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, yahoo_calls, _ = _provider(monkeypatch, tmp_path, yahoo_rows=[])
    # Freeze the provider's recency seam so the empty Yahoo answer is
    # classified at the leading edge (ADR-0033) — the unknown-symbol path the
    # /ohlcv route maps to 404, byte-for-byte the pre-plan taxonomy.
    frozen_now = _BASE + timedelta(days=5)
    monkeypatch.setattr("market_analyser.data.default_provider._now", lambda: frozen_now)

    with pytest.raises(UnknownSymbolError) as excinfo:
        provider.get_ohlcv("ZZZZNOTREAL", "1d", _BASE, frozen_now)

    assert excinfo.value.symbol == "ZZZZNOTREAL"
    assert yahoo_calls == [("ZZZZNOTREAL", "1d")]  # it went down the Yahoo path


# --- (c) per-source history caps: >730d 1h succeeds on Binance, clamps on Yahoo --


def test_1h_window_beyond_730_days_succeeds_for_btcusdt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_start = _BASE - timedelta(days=800)
    provider, _, transport = _provider(
        monkeypatch, tmp_path, binance_klines=[_kline(old_start, i) for i in range(4)]
    )

    bars = provider.get_ohlcv("BTCUSDT", "1h", old_start, _BASE)

    assert len(bars) == 4  # served, not clamped — Binance history is uncapped
    assert all(b.source == "binance" for b in bars)
    # The absolute window went to the wire verbatim (no cap narrowing).
    assert transport.klines_requests[0]["startTime"] == str(int(old_start.timestamp() * 1000))


def test_1h_window_beyond_730_days_still_clamps_for_btc_usd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, yahoo_calls, _ = _provider(monkeypatch, tmp_path)
    old_start = _BASE - timedelta(days=800)

    with pytest.raises(HistoryExceededError, match="730"):
        provider.get_ohlcv("BTC-USD", "1h", old_start, _BASE)

    assert yahoo_calls == []  # the doomed fetch is never attempted


def test_source_cap_seam_is_per_source() -> None:
    """The registry seam itself: Yahoo keeps its caps and its 4h derivation;
    Binance is uncapped and fully native — asserted for every timeframe."""
    assert source_max_history("1h", "yahoo") == timedelta(days=730)
    assert source_max_history("15m", "yahoo") == timedelta(days=60)
    for tf in ("15m", "1h", "4h", "1d", "1w", "1mo"):
        assert source_max_history(tf, "binance") is None
        assert source_resampled_from(tf, "binance") is None
    assert source_resampled_from("4h", "yahoo") == "1h"
    assert source_resampled_from("1h", "yahoo") is None


# --- (d) 4h: native for Binance members, resampled for Yahoo symbols -------------


def test_4h_for_btcusdt_is_served_native_without_resample(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    four_hourly = [_kline(_BASE, 4 * i) for i in range(3)]  # 4h-spaced open times
    provider, yahoo_calls, transport = _provider(monkeypatch, tmp_path, binance_klines=four_hourly)
    resample_calls: list[str] = []

    def resample_spy(bars: list[Any], target: str) -> list[Any]:
        resample_calls.append(target)
        return _real_resample(bars, target=target)

    monkeypatch.setattr("market_analyser.data.default_provider.resample_ohlcv", resample_spy)

    bars = provider.get_ohlcv("BTCUSDT", "4h", _BASE, _BASE + timedelta(hours=12))

    assert resample_calls == []  # no resample call — 4h is native on Binance
    assert [b.timeframe for b in bars] == ["4h", "4h", "4h"]
    assert all(req["interval"] == "4h" for req in transport.klines_requests)
    assert yahoo_calls == []


def test_4h_for_yahoo_symbol_still_resamples_from_1h(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, yahoo_calls, transport = _provider(
        monkeypatch, tmp_path, yahoo_rows=_hourly_yahoo_rows(_BASE, 8)
    )
    resample_calls: list[str] = []

    def resample_spy(bars: list[Any], target: str) -> list[Any]:
        resample_calls.append(target)
        return _real_resample(bars, target=target)

    monkeypatch.setattr("market_analyser.data.default_provider.resample_ohlcv", resample_spy)

    bars = provider.get_ohlcv("BTC-USD", "4h", _BASE, _BASE + timedelta(hours=8))

    assert resample_calls == ["4h"]  # the ADR-0028 derive-on-read path, untouched
    assert yahoo_calls == [("BTC-USD", "1h")]  # fetched at the native base
    assert all(b.timeframe == "4h" for b in bars)
    assert transport.klines_requests == []


# --- membership cache: stale-but-present beats absent -----------------------------


def test_present_cache_file_is_used_without_any_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "binance_exchange_info.json"
    _write_cache(cache, ["BTCUSDT"])
    transport = _static_response(500, b"")  # any request would be a failure
    adapter = _binance_adapter(monkeypatch, transport=transport, cache_path=cache)

    assert adapter.is_known_symbol("BTCUSDT")
    assert adapter.is_known_symbol(" btcusdt ")  # normalized like fetch_ohlcv
    assert not adapter.is_known_symbol("BTC-USD")  # never aliased (ADR-0052)
    assert not adapter.is_known_symbol("AAPL")
    assert transport.attempts == 0  # stale-but-present beats absent: no refresh


def test_absent_cache_lazily_fetches_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "binance_exchange_info.json"
    transport = _FakeBinanceTransport(symbols=["ETHUSDT", "BTCUSDT"])
    adapter = _binance_adapter(monkeypatch, transport=transport, cache_path=cache)

    assert adapter.is_known_symbol("BTCUSDT")
    assert transport.exchange_info_requests == 1

    # Persisted: deterministic content (sorted symbols, upstream's serverTime).
    on_disk = json.loads(cache.read_text(encoding="utf-8"))
    assert on_disk["source"] == "binance"
    assert on_disk["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert (
        on_disk["fetched_at"] == datetime.fromtimestamp(_SERVER_TIME_MS / 1000, tz=UTC).isoformat()
    )

    # A second adapter (fresh process at fixture scale) answers from the file.
    transport2 = _static_response(500, b"")
    adapter2 = _binance_adapter(monkeypatch, transport=transport2, cache_path=cache)
    assert adapter2.is_known_symbol("ETHUSDT")
    assert transport2.attempts == 0


def test_failed_lazy_fetch_memoizes_empty_set_for_the_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreachable exchangeInfo degrades to Yahoo-for-everything (loud 404s
    for Binance-only symbols — the same failure shape as the plan's accepted
    stale-set misroute), and the dead upstream is probed once, not per call."""
    transport = _static_response(500, b"")
    adapter = _binance_adapter(
        monkeypatch, transport=transport, cache_path=tmp_path / "absent.json"
    )

    assert not adapter.is_known_symbol("BTCUSDT")
    assert not adapter.is_known_symbol("ETHUSDT")
    assert transport.attempts == 1  # memoized: one probe per process

    # The provider built on top routes BTCUSDT to Yahoo, which answers loudly.
    yahoo, yahoo_calls = _yahoo_spy(
        [
            {
                "date": "2024-01-02",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000.0,
            },
        ],
    )
    provider = DefaultMarketDataProvider(yahoo=yahoo, binance=adapter)
    provider.get_ohlcv("BTCUSDT", "1d", _BASE, _BASE + timedelta(days=5))
    assert yahoo_calls == [("BTCUSDT", "1d")]


def test_refresh_symbols_is_the_explicit_recovery_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "binance_exchange_info.json"
    _write_cache(cache, ["OLDUSDT"])
    transport = _FakeBinanceTransport(symbols=["BTCUSDT", "NEWUSDT"])
    adapter = _binance_adapter(monkeypatch, transport=transport, cache_path=cache)

    # Stale set in force until the explicit refresh (no TTL, no auto-refresh).
    assert adapter.is_known_symbol("OLDUSDT")
    assert not adapter.is_known_symbol("NEWUSDT")
    assert transport.exchange_info_requests == 0

    refreshed = adapter.refresh_symbols()

    assert refreshed == frozenset({"BTCUSDT", "NEWUSDT"})
    assert adapter.is_known_symbol("NEWUSDT")
    assert not adapter.is_known_symbol("OLDUSDT")
    assert json.loads(cache.read_text(encoding="utf-8"))["symbols"] == [
        "BTCUSDT",
        "NEWUSDT",
    ]


def test_corrupt_cache_file_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "binance_exchange_info.json"
    cache.write_text("{not json", encoding="utf-8")
    transport = _FakeBinanceTransport(symbols=["BTCUSDT"])
    adapter = _binance_adapter(monkeypatch, transport=transport, cache_path=cache)

    assert adapter.is_known_symbol("BTCUSDT")
    assert transport.exchange_info_requests == 1  # corrupt is not "present"


def test_refresh_451_raises_geo_restricted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transport = _static_response(451, b'{"code": 0, "msg": "restricted location"}')
    adapter = _binance_adapter(
        monkeypatch, transport=transport, cache_path=tmp_path / "absent.json"
    )

    with pytest.raises(GeoRestrictedError, match="451"):
        adapter.refresh_symbols()
    assert transport.attempts == 1  # permanent, never retried (ADR-0052)


@pytest.mark.parametrize(
    "payload",
    [
        {"serverTime": _SERVER_TIME_MS},  # symbols list missing
        {"symbols": [{"symbol": "BTCUSDT"}]},  # serverTime missing
        {"serverTime": _SERVER_TIME_MS, "symbols": [{"name": "BTCUSDT"}]},  # entry drift
    ],
)
def test_exchange_info_shape_drift_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict[str, Any]
) -> None:
    transport = _static_response(200, json.dumps(payload).encode("utf-8"))
    adapter = _binance_adapter(
        monkeypatch, transport=transport, cache_path=tmp_path / "absent.json"
    )

    with pytest.raises(BinanceKlinesError, match="exchangeInfo"):
        adapter.refresh_symbols()
