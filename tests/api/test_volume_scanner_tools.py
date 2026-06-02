"""Phase-3 done-when for Plan 0021: the volume-scanner MCP tools.

The bodies are factored into `_volume_breakout_scan_response` /
`_volume_confirmation_response` / `_smart_volume_scan_response` so the scan, skip,
and boundary paths run on a single event loop. One live-MCP-server test covers
registration + transport for all three. A `_SeededProvider` returns canned
per-(symbol, timeframe) bars (honouring the window + `as_of` truncation) and can
be told to raise for specific symbols, exercising graceful degradation.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.analysis.volume import (
    BREAKOUT_PRICE_LOOKBACK,
    BREAKOUT_VOL_MULTIPLE,
    CONFIRMATION_LOOKBACK,
    SMART_RSI_HIGH,
    SMART_RSI_LOW,
    SMART_VOL_MULTIPLE,
)
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.smart_volume import (
    MAX_SCAN_SYMBOLS,
    _smart_volume_scan_response,
)
from market_analyser.api.mcp_tools.volume_breakout import _volume_breakout_scan_response
from market_analyser.api.mcp_tools.volume_confirmation import _volume_confirmation_response
from market_analyser.data.types import (
    Bar,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _mk(
    symbol: str,
    closes: Sequence[float],
    volumes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
) -> list[Bar]:
    """Daily bars ending today from explicit close/volume series; highs/lows
    default to the close (a degenerate but valid OHLC band)."""

    n = len(closes)
    hi = highs if highs is not None else list(closes)
    lo = lows if lows is not None else list(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=hi[i],
            low=lo[i],
            close=closes[i],
            volume=volumes[i],
            source="fixture",
        )
        for i in range(n)
    ]


def _breakout(symbol: str, *, last_volume: float) -> list[Bar]:
    """20 tight-range bars then a 21st that clears the trailing high (101) on a
    volume surge whose multiple scales with `last_volume`."""

    closes = [100.0] * 20 + [110.0]
    volumes = [100.0] * 20 + [last_volume]
    highs = [101.0] * 20 + [111.0]
    lows = [99.0] * 20 + [109.0]
    return _mk(symbol, closes, volumes, highs, lows)


def _drift(symbol: str) -> list[Bar]:
    """A quiet drift inside the range with no volume surge — not a breakout."""

    return _mk(symbol, [100.0] * 20 + [100.5], [100.0] * 21, [101.0] * 21, [99.0] * 21)


def _confirmation(symbol: str, *, up_volume: float, down_volume: float) -> list[Bar]:
    """21 bars netting upward; up bars carry `up_volume`, the down bars
    (i=5/10/15/20) carry `down_volume`."""

    closes = [100.0]
    volumes = [100.0]
    close = 100.0
    for i in range(1, 21):
        if i % 5 == 0:
            close -= 1.0
            volumes.append(down_volume)
        else:
            close += 2.0
            volumes.append(up_volume)
        closes.append(close)
    return _mk(symbol, closes, volumes)


def _oscillating(symbol: str, *, last_volume: float, n: int = 30) -> list[Bar]:
    """Alternating closes → RSI ≈ 50 (inside the smart-volume band); volume surge
    on the last bar."""

    closes: list[float] = []
    close = 100.0
    for i in range(n):
        close += 1.0 if i % 2 == 0 else -1.0
        closes.append(close)
    volumes = [last_volume if i == n - 1 else 100.0 for i in range(n)]
    return _mk(symbol, closes, volumes)


def _uptrend(symbol: str, *, last_volume: float, n: int = 30) -> list[Bar]:
    """Monotonic rise → RSI ≈ 100 (above the band); the same last-bar surge as
    `_oscillating`."""

    closes = [100.0 + i for i in range(n)]
    volumes = [last_volume if i == n - 1 else 100.0 for i in range(n)]
    return _mk(symbol, closes, volumes)


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window
    and truncated at `as_of`. Symbols in `error_symbols` raise (a fetch failure to
    exercise graceful degradation). Every other Protocol method raises."""

    def __init__(
        self,
        bars_by_key: dict[tuple[str, str], Sequence[Bar]],
        error_symbols: Iterable[str] = (),
    ) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}
        self._errors = set(error_symbols)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        if symbol in self._errors:
            raise RuntimeError(f"simulated fetch failure for {symbol}")
        bars = [b for b in self._by_key.get((symbol, timeframe), []) if start <= b.event_ts <= end]
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return bars

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# volume_breakout scan                                                          #
# --------------------------------------------------------------------------- #


def test_breakout_scan_returns_only_breakouts_sorted() -> None:
    provider = _SeededProvider(
        {
            ("A", "1d"): _breakout("A", last_volume=300.0),  # multiple ~2.7
            ("B", "1d"): _drift("B"),  # no breakout
            ("C", "1d"): _breakout("C", last_volume=400.0),  # multiple ~3.5
        }
    )
    resp = asyncio.run(
        _volume_breakout_scan_response(
            provider=provider,
            symbols=["A", "B", "C"],
            timeframe="1d",
            vol_multiple=BREAKOUT_VOL_MULTIPLE,
            price_lookback=BREAKOUT_PRICE_LOOKBACK,
            as_of=None,
        )
    )
    # B excluded; C before A (multiple descending).
    assert [m.symbol for m in resp.matches] == ["C", "A"]
    assert resp.matches[0].volume_multiple is not None
    assert resp.matches[1].volume_multiple is not None
    assert resp.matches[0].volume_multiple > resp.matches[1].volume_multiple
    for m in resp.matches:
        assert m.is_breakout is True
        assert m.direction == "bullish"
        assert m.broken_level == 101.0
    assert resp.skipped == []
    assert resp.scanned_at.tzinfo is not None


def test_breakout_scan_skips_missing_and_failed_symbols() -> None:
    provider = _SeededProvider(
        {("A", "1d"): _breakout("A", last_volume=300.0)},
        error_symbols={"BOOM"},
    )
    resp = asyncio.run(
        _volume_breakout_scan_response(
            provider=provider,
            symbols=["A", "MISSING", "BOOM"],
            timeframe="1d",
            vol_multiple=BREAKOUT_VOL_MULTIPLE,
            price_lookback=BREAKOUT_PRICE_LOOKBACK,
            as_of=None,
        )
    )
    assert [m.symbol for m in resp.matches] == ["A"]  # the rest still scanned
    assert sorted(resp.skipped) == ["BOOM", "MISSING"]  # no-bars + fetch-error both skipped


@pytest.mark.parametrize(
    ("symbols", "timeframe"),
    [
        ([], "1d"),  # empty list
        (["A", "B"], "5m"),  # unsupported timeframe
        ([f"S{i}" for i in range(MAX_SCAN_SYMBOLS + 1)], "1d"),  # over the cap
    ],
)
def test_breakout_scan_boundary_validation(symbols: list[str], timeframe: str) -> None:
    provider = _SeededProvider({})
    with pytest.raises(ValueError):
        asyncio.run(
            _volume_breakout_scan_response(
                provider=provider,
                symbols=symbols,
                timeframe=timeframe,
                vol_multiple=BREAKOUT_VOL_MULTIPLE,
                price_lookback=BREAKOUT_PRICE_LOOKBACK,
                as_of=None,
            )
        )


# --------------------------------------------------------------------------- #
# volume_confirmation detail                                                    #
# --------------------------------------------------------------------------- #


def test_confirmation_detail_returns_score_and_figures() -> None:
    provider = _SeededProvider({("A", "1d"): _confirmation("A", up_volume=300.0, down_volume=50.0)})
    resp = asyncio.run(
        _volume_confirmation_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            lookback=CONFIRMATION_LOOKBACK,
            as_of=None,
        )
    )
    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.result.symbol == "A"
    assert resp.result.direction == "bullish"
    assert resp.result.score > 0.9
    assert resp.result.confirmed is True
    assert resp.result.supportive_volume > resp.result.opposing_volume
    assert resp.scanned_at.tzinfo is not None


def test_confirmation_detail_no_bars() -> None:
    provider = _SeededProvider({})
    resp = asyncio.run(
        _volume_confirmation_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            lookback=CONFIRMATION_LOOKBACK,
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


# --------------------------------------------------------------------------- #
# smart_volume scan                                                             #
# --------------------------------------------------------------------------- #


def test_smart_volume_scan_returns_only_qualifying() -> None:
    provider = _SeededProvider(
        {
            ("A", "1d"): _oscillating("A", last_volume=200.0),  # surge + RSI in band
            ("B", "1d"): _uptrend("B", last_volume=200.0),  # surge but RSI above band
        }
    )
    resp = asyncio.run(
        _smart_volume_scan_response(
            provider=provider,
            symbols=["A", "B"],
            timeframe="1d",
            rsi_low=SMART_RSI_LOW,
            rsi_high=SMART_RSI_HIGH,
            vol_multiple=SMART_VOL_MULTIPLE,
            as_of=None,
        )
    )
    assert [m.symbol for m in resp.matches] == ["A"]
    assert resp.matches[0].qualifies is True
    assert resp.skipped == []


def test_smart_volume_scan_rejects_bad_band() -> None:
    provider = _SeededProvider({})
    with pytest.raises(ValueError):
        asyncio.run(
            _smart_volume_scan_response(
                provider=provider,
                symbols=["A"],
                timeframe="1d",
                rsi_low=70.0,
                rsi_high=30.0,  # inverted band
                vol_multiple=SMART_VOL_MULTIPLE,
                as_of=None,
            )
        )


def test_smart_volume_scan_skips_missing_and_failed_symbols() -> None:
    # smart_volume carries its own copy of the cap + skip loop (deliberately not
    # shared with volume_breakout), so it needs its own coverage of the path.
    provider = _SeededProvider(
        {("A", "1d"): _oscillating("A", last_volume=200.0)},
        error_symbols={"BOOM"},
    )
    resp = asyncio.run(
        _smart_volume_scan_response(
            provider=provider,
            symbols=["A", "MISSING", "BOOM"],
            timeframe="1d",
            rsi_low=SMART_RSI_LOW,
            rsi_high=SMART_RSI_HIGH,
            vol_multiple=SMART_VOL_MULTIPLE,
            as_of=None,
        )
    )
    assert [m.symbol for m in resp.matches] == ["A"]  # the rest still scanned
    assert sorted(resp.skipped) == ["BOOM", "MISSING"]  # no-bars + fetch-error both skipped


@pytest.mark.parametrize(
    ("symbols", "timeframe"),
    [
        ([], "1d"),  # empty list
        (["A", "B"], "5m"),  # unsupported timeframe
        ([f"S{i}" for i in range(MAX_SCAN_SYMBOLS + 1)], "1d"),  # over the cap
    ],
)
def test_smart_volume_scan_boundary_validation(symbols: list[str], timeframe: str) -> None:
    provider = _SeededProvider({})
    with pytest.raises(ValueError):
        asyncio.run(
            _smart_volume_scan_response(
                provider=provider,
                symbols=symbols,
                timeframe=timeframe,
                rsi_low=SMART_RSI_LOW,
                rsi_high=SMART_RSI_HIGH,
                vol_multiple=SMART_VOL_MULTIPLE,
                as_of=None,
            )
        )


# --------------------------------------------------------------------------- #
# Live MCP server: registration + transport for all three                       #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def app(mcp_secret: str, annotations_repo: AnnotationsRepository) -> FastAPI:
    provider = _SeededProvider(
        {
            ("A", "1d"): _breakout("A", last_volume=300.0),
            ("CONF", "1d"): _confirmation("CONF", up_volume=300.0, down_volume=50.0),
        }
    )
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=provider,
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="error", access_log=False)
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("uvicorn server failed to start within 5s")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@asynccontextmanager
async def _mcp_session(url: str, bearer: str) -> AsyncIterator[ClientSession]:
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=httpx.Timeout(30.0),
        ) as http_client,
        streamable_http_client(f"{url}/mcp", http_client=http_client) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def test_scanner_tools_registered_and_callable(live_server: str, mcp_secret: str) -> None:
    """All three tools are registered under their documented names and reachable
    over the real MCP transport, returning the documented response shapes."""

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            listed = {t.name for t in (await session.list_tools()).tools}
            assert {"volume_breakout", "volume_confirmation", "smart_volume"} <= listed

            breakout = await session.call_tool(
                "volume_breakout", {"symbols": ["A"], "timeframe": "1d"}
            )
            confirmation = await session.call_tool(
                "volume_confirmation", {"symbol": "CONF", "timeframe": "1d"}
            )
            smart = await session.call_tool("smart_volume", {"symbols": ["A"], "timeframe": "1d"})
            for result in (breakout, confirmation, smart):
                assert not result.isError, f"tool errored: {result.content}"
                assert result.structuredContent is not None

            breakout_sc = breakout.structuredContent
            assert breakout_sc is not None
            matches = breakout_sc["matches"]
            assert isinstance(matches, list)
            assert {m["symbol"] for m in matches} == {"A"}
            assert breakout_sc["scanned_at"]

            confirmation_sc = confirmation.structuredContent
            assert confirmation_sc is not None
            assert confirmation_sc["result"] is not None

    asyncio.run(_run())
