"""Done-when for Plan 0109 phase 5: the unified `volume_read(kind=…)` tool (ADR-0104).

Folds `volume_confirmation` (Plan 0021) and `counter_trend_volume` (Plan 0090) into
`kind` modes of one tool. Both returned `{result, partial_reason, scanned_at}`, so — like
phases 1/4 — the fold flattens into `{kind, result, partial_reason, scanned_at}` (the
mode-union in the `result` field). Each mode section reproduces its predecessor's
assertions against `_volume_read_response`, including the counter-trend read's anchoring
to the canonical snapshot trend (ADR-0083, pinned against `analyze_symbol`) and its
trailing byte-equivalence to the direct compute. One live-MCP-server test covers
registration + transport under the new name.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from market_analyser.analysis.types import CounterTrendVolume, VolumeConfirmation
from market_analyser.analysis.volume import COUNTER_TREND_LOOKBACK, counter_trend_volume
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.analyze_symbol import _analyze_symbol_response
from market_analyser.api.mcp_tools.volume_read import (
    VolumeReadResponse,
    _volume_read_response,
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
_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _result_of[M: BaseModel](resp: VolumeReadResponse, cls: type[M]) -> M:
    """Narrow the discriminated `result` to the mode's concrete model — the envelope's
    `result` is fixed by `kind`, so a populated result is always `cls`."""

    assert resp.result is not None
    assert isinstance(resp.result, cls)
    return resp.result


def _mk(symbol: str, closes: Sequence[float], volumes: Sequence[float]) -> list[Bar]:
    """Daily bars ending today from explicit close/volume series (high/low collapse to
    the close — the volume reads are close/volume driven)."""

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


def _confirmation_bars(symbol: str, *, up_volume: float, down_volume: float) -> list[Bar]:
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


def _uptrend_with_counter_bars(symbol: str, n: int = 90) -> list[Bar]:
    """A steady rise (a clear UP trend) where most bars are bullish intrabar but every
    5th recent bar is a heavier bearish intrabar down-bar — still closing above the prior
    close, so the trend stays up while the window carries genuine counter-trend bars."""

    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + i
        bearish = i >= n - 20 and i % 5 == 0
        if bearish:
            o, c, v = base + 0.4, base - 0.4, 300.0
        else:
            o, c, v = base - 0.4, base + 0.4, 100.0
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_END - timedelta(days=n - 1 - i),
                open=o,
                high=max(o, c) + 0.3,
                low=min(o, c) - 0.3,
                close=c,
                volume=v,
                source="fixture",
            )
        )
    return bars


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window and
    truncated at `as_of`. Every other Protocol method raises."""

    def __init__(self, bars_by_key: Mapping[tuple[str, str], Sequence[Bar]]) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
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
# kind="confirmation" (was volume_confirmation)                                 #
# --------------------------------------------------------------------------- #


def test_confirmation_returns_score_and_figures() -> None:
    provider = _SeededProvider(
        {("A", "1d"): _confirmation_bars("A", up_volume=300.0, down_volume=50.0)}
    )
    resp = asyncio.run(
        _volume_read_response(
            provider=provider, symbol="A", timeframe="1d", kind="confirmation", as_of=None
        )
    )
    assert resp.kind == "confirmation"
    assert resp.partial_reason is None
    result = _result_of(resp, VolumeConfirmation)
    assert result.symbol == "A"
    assert result.direction == "bullish"
    assert result.score > 0.9
    assert result.confirmed is True
    assert result.supportive_volume > result.opposing_volume
    assert resp.scanned_at.tzinfo is not None


def test_confirmation_no_bars() -> None:
    resp = asyncio.run(
        _volume_read_response(
            provider=_SeededProvider({}),
            symbol="A",
            timeframe="1d",
            kind="confirmation",
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


# --------------------------------------------------------------------------- #
# kind="counter_trend" (was counter_trend_volume)                               #
# --------------------------------------------------------------------------- #


def test_counter_trend_anchors_to_snapshot_trend() -> None:
    """The per-bar decomposition's trend anchor is exactly the label `analyze_symbol`
    reports on the same bars (ADR-0083), and heavy down-bars register counter-trend."""

    bars = _uptrend_with_counter_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})

    resp = asyncio.run(
        _volume_read_response(
            provider=provider, symbol="A", timeframe="1d", kind="counter_trend", as_of=None
        )
    )
    assert resp.partial_reason is None
    result = _result_of(resp, CounterTrendVolume)
    assert result.symbol == "A"
    assert len(result.bars) == COUNTER_TREND_LOOKBACK

    analyzed = asyncio.run(
        _analyze_symbol_response(
            provider=provider, symbol="A", timeframe="1d", lookback="1y", as_of=None
        )
    )
    assert analyzed.snapshot is not None
    assert result.trend == analyzed.snapshot.trend  # anchored to the canonical trend
    assert any(b.is_counter_trend for b in result.bars)


def test_counter_trend_no_bars() -> None:
    resp = asyncio.run(
        _volume_read_response(
            provider=_SeededProvider({}),
            symbol="A",
            timeframe="1d",
            kind="counter_trend",
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_counter_trend_as_of_is_trailing() -> None:
    """`as_of` truncates to bars at-or-before it — the decomposition never sees a future
    bar and matches a direct computation on the truncated series."""

    bars = _uptrend_with_counter_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    as_of = bars[70].event_ts

    resp = asyncio.run(
        _volume_read_response(
            provider=provider, symbol="A", timeframe="1d", kind="counter_trend", as_of=as_of
        )
    )
    result = _result_of(resp, CounterTrendVolume)
    assert all(b.ts <= as_of for b in result.bars)

    truncated = [b for b in bars if b.event_ts <= as_of]
    direct = counter_trend_volume(truncated, result.trend, COUNTER_TREND_LOOKBACK)
    assert result.bars == direct.bars  # byte-equivalent to the direct compute
    assert result.counter_trend_volume_share == direct.counter_trend_volume_share

    full = asyncio.run(
        _volume_read_response(
            provider=provider, symbol="A", timeframe="1d", kind="counter_trend", as_of=None
        )
    )
    full_result = _result_of(full, CounterTrendVolume)
    assert full_result.bars[-1].ts != result.bars[-1].ts  # future bars excluded at as_of


# --------------------------------------------------------------------------- #
# Boundary validation + live MCP server registration                            #
# --------------------------------------------------------------------------- #


def test_volume_read_boundary_validation() -> None:
    provider = _SeededProvider({})
    for symbol, timeframe in (("", "1d"), ("A", "5m")):
        with pytest.raises(ValueError):
            asyncio.run(
                _volume_read_response(
                    provider=provider,
                    symbol=symbol,
                    timeframe=timeframe,
                    kind="confirmation",
                    as_of=None,
                )
            )


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
        {("CONF", "1d"): _confirmation_bars("CONF", up_volume=300.0, down_volume=50.0)}
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


def test_volume_read_registered_and_callable(live_server: str, mcp_secret: str) -> None:
    """`volume_read` is registered under its documented name; the two retired names are
    gone; a call returns the discriminated `{kind, result, …}` envelope."""

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            listed = {t.name for t in (await session.list_tools()).tools}
            assert "volume_read" in listed
            assert listed.isdisjoint({"volume_confirmation", "counter_trend_volume"})

            conf = await session.call_tool(
                "volume_read",
                {"symbol": "CONF", "timeframe": "1d", "kind": "confirmation"},
            )
            assert not conf.isError, f"tool errored: {conf.content}"
            sc = conf.structuredContent
            assert sc is not None
            assert sc["kind"] == "confirmation"
            assert sc["result"] is not None
            assert sc["result"]["symbol"] == "CONF"
            assert sc["scanned_at"]

    asyncio.run(_run())
