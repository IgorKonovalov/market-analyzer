"""Done-when for the `volume_confirmation` single-symbol read (Plan 0021 phase 3).

The `volume_breakout` and `smart_volume` scans this file once also covered were folded
into `scan_watchlist(rank_by=…)` by Plan 0109 phase 1 — their assertions now live in
`test_scan_watchlist_tool.py`. `volume_confirmation` stays a standalone tool until
Plan 0109 phase 5 folds it into `volume_read`; its body is factored into
`_volume_confirmation_response` so the detail + no-bars paths run on a single event
loop, and one live-MCP-server test covers registration + transport. A `_SeededProvider`
returns canned per-(symbol, timeframe) bars (honouring the window + `as_of`
truncation).
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

from market_analyser.analysis.volume import CONFIRMATION_LOOKBACK
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.volume_confirmation import _volume_confirmation_response
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
_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _mk(
    symbol: str,
    closes: Sequence[float],
    volumes: Sequence[float],
) -> list[Bar]:
    """Daily bars ending today from explicit close/volume series; highs/lows collapse
    to the close (a degenerate but valid OHLC band — the confirmation read is
    close/volume driven)."""

    n = len(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=closes[i],
            low=closes[i],
            close=closes[i],
            volume=volumes[i],
            source="fixture",
        )
        for i in range(n)
    ]


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
# Live MCP server: registration + transport                                     #
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
        {("CONF", "1d"): _confirmation("CONF", up_volume=300.0, down_volume=50.0)}
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


def test_confirmation_tool_registered_and_callable(live_server: str, mcp_secret: str) -> None:
    """`volume_confirmation` is registered under its documented name and reachable over
    the real MCP transport, returning the documented response shape."""

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            listed = {t.name for t in (await session.list_tools()).tools}
            assert "volume_confirmation" in listed

            confirmation = await session.call_tool(
                "volume_confirmation", {"symbol": "CONF", "timeframe": "1d"}
            )
            assert not confirmation.isError, f"tool errored: {confirmation.content}"
            assert confirmation.structuredContent is not None
            assert confirmation.structuredContent["result"] is not None

    asyncio.run(_run())
