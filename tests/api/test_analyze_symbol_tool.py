"""Phase-4 done-when for Plan 0018: the `analyze_symbol` MCP tool.

The body is factored into `_analyze_symbol_response` so the fetch / empty-cache /
as_of paths are exercised on a single event loop. One live-MCP-server test covers
registration + transport. A controllable `_SeededProvider` returns canned bars
(honouring `as_of` truncation) and satisfies the full `MarketDataProvider`
Protocol so it can also back a real app.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.analyze_symbol import (
    _analyze_symbol_response,
    _parse_lookback,
)
from market_analyser.data.types import (
    Bar,
    MacroContext,
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


def _seed_bars(symbol: str = "AAPL", n: int = 80) -> list[Bar]:
    """A rising-with-wobble daily series ending today, long enough that every
    indicator (incl. ADX and the EMA-50 trend leg) is defined."""

    end = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + 0.8 * i + (1.5 if i % 3 == 0 else -1.0)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=end - timedelta(days=n - 1 - i),
                open=base,
                high=base + 1.5,
                low=base - 1.5,
                close=base + 0.6,
                volume=1_000_000.0,
                source="fixture",
            )
        )
    return bars


class _SeededProvider:
    """Returns its canned bars for the seeded symbol, truncated to `event_ts <=
    as_of` when `as_of` is set (the anti-lookahead replay the real provider gives
    via the cache). Every other Protocol method raises."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)
        self._symbol = bars[0].symbol if bars else ""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        if symbol != self._symbol:
            return []
        bars = self._bars
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return list(bars)

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

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
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
# Lookback parsing                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "days"),
    [("6mo", 180), ("1y", 365), ("30d", 30), ("2w", 14), ("1d", 1)],
)
def test_parse_lookback_valid(text: str, days: int) -> None:
    assert _parse_lookback(text) == timedelta(days=days)


@pytest.mark.parametrize("text", ["banana", "6", "mo", "-3d", "0d", "6 mo", "6M"])
def test_parse_lookback_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        _parse_lookback(text)


# --------------------------------------------------------------------------- #
# Tool body                                                                    #
# --------------------------------------------------------------------------- #


def test_happy_path_returns_full_snapshot() -> None:
    provider = _SeededProvider(_seed_bars())

    resp = asyncio.run(
        _analyze_symbol_response(
            provider=provider, symbol="AAPL", timeframe="1d", lookback="6mo", as_of=None
        )
    )

    assert resp.partial_reason is None
    assert resp.snapshot is not None
    snap = resp.snapshot
    assert snap.symbol == "AAPL"
    assert snap.timeframe == "1d"
    assert snap.trend.value in {"up", "down", "sideways"}
    assert snap.momentum.value in {"overbought", "bullish", "neutral", "bearish", "oversold"}
    for key in ("rsi", "macd", "bb_upper", "atr", "adx", "supertrend"):
        assert key in snap.indicators
        assert snap.indicators[key] is not None
    # Plan 0027: volume measures ride in `indicators`, stance is a top-level field.
    assert snap.volume_stance.value in {"heavy", "normal", "light"}
    for key in ("volume", "vol_sma20", "rel_volume", "vol_pct90", "obv", "obv_slope", "vwap"):
        assert key in snap.indicators
        assert snap.indicators[key] is not None
    assert set(snap.support_resistance) == {"support", "resistance"}
    assert isinstance(snap.recent_patterns, list)
    assert resp.analyzed_at.tzinfo is not None  # UTC-aware provenance stamp


def test_as_of_replay_truncates_without_future_leak() -> None:
    bars = _seed_bars()
    provider = _SeededProvider(bars)
    as_of = bars[50].event_ts

    resp = asyncio.run(
        _analyze_symbol_response(
            provider=provider, symbol="AAPL", timeframe="1d", lookback="6mo", as_of=as_of
        )
    )
    assert resp.snapshot is not None
    assert resp.snapshot.as_of == as_of

    expected = condition_snapshot(bars[:51], "1d")
    assert resp.snapshot.indicators == expected.indicators
    assert resp.snapshot.trend == expected.trend
    # The truncated read differs from the full-series read -> no future leak.
    full = condition_snapshot(bars, "1d")
    assert resp.snapshot.indicators["rsi"] != full.indicators["rsi"]


def test_empty_cache_returns_no_bars_shape() -> None:
    provider = _SeededProvider([])  # nothing cached

    resp = asyncio.run(
        _analyze_symbol_response(
            provider=provider, symbol="AAPL", timeframe="1d", lookback="6mo", as_of=None
        )
    )
    assert resp.snapshot is None
    assert resp.partial_reason == "no_bars"
    assert resp.message is not None and "no cached bars" in resp.message
    assert resp.analyzed_at.tzinfo is not None


@pytest.mark.parametrize(
    ("symbol", "timeframe", "lookback"),
    [
        ("", "1d", "6mo"),  # empty symbol
        ("AAPL", "5m", "6mo"),  # unsupported timeframe
        ("AAPL", "1d", "banana"),  # malformed lookback
    ],
)
def test_boundary_validation_rejects_bad_input(symbol: str, timeframe: str, lookback: str) -> None:
    provider = _SeededProvider(_seed_bars())
    with pytest.raises(ValueError):
        asyncio.run(
            _analyze_symbol_response(
                provider=provider,
                symbol=symbol,
                timeframe=timeframe,
                lookback=lookback,
                as_of=None,
            )
        )


# --------------------------------------------------------------------------- #
# Live MCP server: registration + transport                                    #
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
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_SeededProvider(_seed_bars()),
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


def test_analyze_symbol_via_mcp_returns_snapshot(live_server: str, mcp_secret: str) -> None:
    """The tool is registered and reachable over the real MCP transport, and a
    seeded cache yields a non-null snapshot with the documented top-level keys."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "analyze_symbol", {"symbol": "AAPL", "timeframe": "1d"}
            )
            assert not result.isError, f"analyze_symbol errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    assert payload["partial_reason"] is None
    assert payload["analyzed_at"]
    snapshot = payload["snapshot"]
    assert isinstance(snapshot, dict)
    assert {
        "trend",
        "momentum",
        "volume_stance",
        "indicators",
        "support_resistance",
        "recent_patterns",
    } <= set(snapshot)
