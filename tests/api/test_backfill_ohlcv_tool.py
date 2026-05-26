"""Plan 0013 phase 2 done-when: get_ohlcv `backfill_async` + the new backfill_ohlcv tool.

The tool bodies are factored into `_get_ohlcv_response` / `_backfill_ohlcv_response`
so the scheduling + event behaviour is exercised on a single event loop (reliable
event / no-event assertions, no cross-thread bus reads). Two live-MCP-server tests
cover registration + transport + the agent-facing docstring.

The backfill tools need the narrow `SupportsBackfill` capability (get_ohlcv +
cache-only coverage); `_CoverageProvider` is a controllable fake that provides it
plus the full MarketDataProvider Protocol (so it can also back a real app).
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.api.app import create_app
from market_analyser.api.backfill_response import BackfillOhlcvResponse, GetOhlcvResponse
from market_analyser.api.events import Envelope, EventBus
from market_analyser.api.mcp_app import _backfill_ohlcv_response, _get_ohlcv_response
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.errors import RateLimitedError
from market_analyser.data.types import (
    Bar,
    Coverage,
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
_T0 = datetime(2026, 4, 1, tzinfo=UTC)
_T1 = datetime(2026, 5, 1, tzinfo=UTC)


def _sample_bar(day: int = 15) -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 4, day, tzinfo=UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        source="yahoo",
    )


class _CoverageProvider:
    """Controllable fake: configured cached bars + gaps for `coverage()`, and a
    configured fetch result (bars or an exception) for `get_ohlcv()`. An optional
    `gate` blocks the fetch so tests can prove the tool doesn't wait on it."""

    def __init__(
        self,
        *,
        cached: Sequence[Bar],
        gaps: Sequence[tuple[datetime, datetime]],
        fetched: Sequence[Bar] | Exception,
        gate: threading.Event | None = None,
    ) -> None:
        self._cached = list(cached)
        self._gaps = list(gaps)
        self._fetched = fetched
        self._gate = gate
        self.fetch_calls = 0

    def coverage(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> Coverage:
        return Coverage(cached=list(self._cached), gaps=list(self._gaps))

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.fetch_calls += 1
        if self._gate is not None:
            self._gate.wait(timeout=5.0)
        if isinstance(self._fetched, Exception):
            raise self._fetched
        return list(self._fetched)

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
# Coordinator-level: the event sequence around a scheduled backfill            #
# --------------------------------------------------------------------------- #


def test_schedule_publishes_started_then_backfilled() -> None:
    """A successful backfill publishes started (before the fetch) then backfilled
    (after), in that order, with bars_added counting the new bars."""

    async def run() -> list[Envelope]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _CoverageProvider(cached=[], gaps=[(_T0, _T1)], fetched=[_sample_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        await coord.schedule("AAPL", "1d", _T0, _T1)
        first = await asyncio.wait_for(sub.next(), timeout=2)
        second = await asyncio.wait_for(sub.next(), timeout=2)
        sub.close()
        return [first, second]

    started, backfilled = asyncio.run(run())
    assert started.type == "ohlcv.backfill_started"
    assert backfilled.type == "ohlcv.backfilled"
    assert backfilled.payload["bars_added"] == 1


def test_schedule_failure_publishes_backfill_failed_with_reason() -> None:
    """A typed upstream error during the fetch publishes backfill_failed with the
    mapped reason and the exception message."""

    async def run() -> list[Envelope]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _CoverageProvider(
            cached=[],
            gaps=[(_T0, _T1)],
            fetched=RateLimitedError("yahoo: rate limited (HTTP 429)", retry_after_seconds=60),
        )
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        await coord.schedule("AAPL", "1d", _T0, _T1)
        first = await asyncio.wait_for(sub.next(), timeout=2)
        second = await asyncio.wait_for(sub.next(), timeout=2)
        sub.close()
        return [first, second]

    started, failed = asyncio.run(run())
    assert started.type == "ohlcv.backfill_started"
    assert failed.type == "ohlcv.backfill_failed"
    assert failed.payload["reason"] == "rate_limited"
    assert "rate limited" in failed.payload["message"]


# --------------------------------------------------------------------------- #
# backfill_ohlcv tool body                                                     #
# --------------------------------------------------------------------------- #


def test_backfill_ohlcv_schedules_on_gaps_and_reports_them() -> None:
    """Fresh cache → started=True with the gap windows, and the started/backfilled
    events fire on the bus."""

    async def run() -> tuple[BackfillOhlcvResponse, list[Envelope]]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _CoverageProvider(cached=[], gaps=[(_T0, _T1)], fetched=[_sample_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        resp = await _backfill_ohlcv_response(
            coordinator=coord, symbol="AAPL", timeframe="1d", start=_T0, end=_T1
        )
        events = [await asyncio.wait_for(sub.next(), timeout=2) for _ in range(2)]
        sub.close()
        return resp, events

    resp, events = asyncio.run(run())
    assert resp.started is True
    assert len(resp.gaps) == 1
    assert resp.gaps[0].start == _T0
    assert resp.message
    assert [e.type for e in events] == ["ohlcv.backfill_started", "ohlcv.backfilled"]


def test_backfill_ohlcv_already_complete_publishes_no_event() -> None:
    """Cache already covers the window → started=False, empty gaps, and NO event
    is published (the tool does not schedule when there is nothing to fetch)."""

    async def run() -> tuple[BackfillOhlcvResponse, int]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _CoverageProvider(cached=[_sample_bar()], gaps=[], fetched=[])
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        resp = await _backfill_ohlcv_response(
            coordinator=coord, symbol="AAPL", timeframe="1d", start=_T0, end=_T1
        )
        await asyncio.sleep(0)  # give any (erroneously) scheduled task a chance to run
        qsize = sub.queue.qsize()
        sub.close()
        return resp, qsize

    resp, qsize = asyncio.run(run())
    assert resp.started is False
    assert resp.gaps == []
    assert "already" in resp.message.lower()
    assert qsize == 0


def test_backfill_ohlcv_returns_before_the_fetch_completes() -> None:
    """The tool returns started=True immediately; backfilled only fires once the
    (gated) fetch resolves — proving the tool does not block on the fetch."""

    async def run() -> tuple[BackfillOhlcvResponse, str, bool, str]:
        gate = threading.Event()
        bus = EventBus()
        sub = bus.subscribe()
        provider = _CoverageProvider(
            cached=[], gaps=[(_T0, _T1)], fetched=[_sample_bar()], gate=gate
        )
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        resp = await _backfill_ohlcv_response(
            coordinator=coord, symbol="AAPL", timeframe="1d", start=_T0, end=_T1
        )
        started = await asyncio.wait_for(sub.next(), timeout=2)
        backfilled_pending = sub.queue.empty()  # fetch is gated → not yet
        gate.set()
        finished = await asyncio.wait_for(sub.next(), timeout=2)
        sub.close()
        return resp, started.type, backfilled_pending, finished.type

    resp, started_type, backfilled_pending, finished_type = asyncio.run(run())
    assert resp.started is True
    assert started_type == "ohlcv.backfill_started"
    assert backfilled_pending is True
    assert finished_type == "ohlcv.backfilled"


@pytest.mark.parametrize(
    ("symbol", "timeframe", "start", "end"),
    [
        ("AAPL", "5m", _T0, _T1),  # unsupported timeframe
        ("", "1d", _T0, _T1),  # empty symbol
        ("AAPL", "1d", _T1, _T0),  # end < start
    ],
)
def test_backfill_ohlcv_rejects_invalid_input(
    symbol: str, timeframe: str, start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            _backfill_ohlcv_response(
                coordinator=None, symbol=symbol, timeframe=timeframe, start=start, end=end
            )
        )


# --------------------------------------------------------------------------- #
# get_ohlcv backfill_async tool body                                           #
# --------------------------------------------------------------------------- #


def test_get_ohlcv_backfill_async_miss_returns_pending_and_schedules() -> None:
    """Cache miss + backfill_async=True → returns the (empty) cached bars
    immediately with partial_reason='backfill_async_pending', and the backfill
    events fire."""

    async def run() -> tuple[GetOhlcvResponse, list[str]]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _CoverageProvider(cached=[], gaps=[(_T0, _T1)], fetched=[_sample_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        resp = await _get_ohlcv_response(
            provider=provider,
            coordinator=coord,
            symbol="AAPL",
            timeframe="1d",
            start=_T0,
            end=_T1,
            backfill_async=True,
        )
        types = [(await asyncio.wait_for(sub.next(), timeout=2)).type for _ in range(2)]
        sub.close()
        return resp, types

    resp, types = asyncio.run(run())
    assert resp.bars == []
    assert resp.partial_reason == "backfill_async_pending"
    assert resp.message
    assert types == ["ohlcv.backfill_started", "ohlcv.backfilled"]


def test_get_ohlcv_backfill_async_hit_returns_cached_no_event() -> None:
    """Cache hit (no gaps) + backfill_async=True → cached bars, partial_reason
    None, and NO event published."""

    async def run() -> tuple[GetOhlcvResponse, int]:
        bus = EventBus()
        sub = bus.subscribe()
        bar = _sample_bar()
        provider = _CoverageProvider(cached=[bar], gaps=[], fetched=[])
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        resp = await _get_ohlcv_response(
            provider=provider,
            coordinator=coord,
            symbol="AAPL",
            timeframe="1d",
            start=_T0,
            end=_T1,
            backfill_async=True,
        )
        await asyncio.sleep(0)
        qsize = sub.queue.qsize()
        sub.close()
        return resp, qsize

    resp, qsize = asyncio.run(run())
    assert len(resp.bars) == 1
    assert resp.partial_reason is None
    assert resp.message is None
    assert qsize == 0


def test_get_ohlcv_sync_path_preserved() -> None:
    """backfill_async=False (default) returns the merged result synchronously with
    partial_reason None — today's behaviour, just wrapped in the new shape."""

    async def run() -> GetOhlcvResponse:
        provider = _CoverageProvider(cached=[], gaps=[(_T0, _T1)], fetched=[_sample_bar()])
        return await _get_ohlcv_response(
            provider=provider,
            coordinator=None,
            symbol="AAPL",
            timeframe="1d",
            start=_T0,
            end=_T1,
            backfill_async=False,
        )

    resp = asyncio.run(run())
    assert len(resp.bars) == 1
    assert resp.partial_reason is None
    assert resp.message is None


# --------------------------------------------------------------------------- #
# Live MCP server: registration + transport + docstring                        #
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
    provider = _CoverageProvider(cached=[], gaps=[(_T0, _T1)], fetched=[_sample_bar()])
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


def test_backfill_ohlcv_via_mcp_returns_started(live_server: str, mcp_secret: str) -> None:
    """The tool is registered and reachable over the real MCP transport; a fresh
    cache yields started=True with the gap window."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "backfill_ohlcv",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "start": "2026-04-01T00:00:00+00:00",
                    "end": "2026-05-01T00:00:00+00:00",
                },
            )
            assert not result.isError, f"backfill_ohlcv errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    assert payload["started"] is True
    assert isinstance(payload["gaps"], list)
    assert len(payload["gaps"]) == 1
    assert payload["message"]


def test_backfill_ohlcv_description_mentions_background_and_events(
    live_server: str, mcp_secret: str
) -> None:
    """Done-when: the description names fetch, background, and ohlcv.backfilled so
    the agent knows what the tool does and which events to expect."""

    async def _run() -> str:
        async with _mcp_session(live_server, mcp_secret) as session:
            tools = await session.list_tools()
            tool = next(t for t in tools.tools if t.name == "backfill_ohlcv")
            return tool.description or ""

    description = asyncio.run(_run())
    assert "fetch" in description
    assert "background" in description
    assert "ohlcv.backfilled" in description
