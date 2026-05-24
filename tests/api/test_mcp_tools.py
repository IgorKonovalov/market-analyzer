"""Plan 0006 phase 4 done-when: the three production MCP tools work end-to-end.

Covers:
- `get_ohlcv` returns cached bars to an MCP client connected with the MCP bearer.
- `write_annotation` persists a row visible to subsequent `list_annotations` and
  to the renderer's GET /annotations (cross-surface consistency).
- `write_annotation` with `kind="bogus"` surfaces an MCP-level error rather
  than silently dropping or inserting garbage.
- `list_annotations` filters by the same window semantics as the HTTP route
  (boundary-inclusive on both ends, out-of-window excluded).
- The `ping` tool from phase 1 is no longer registered.

These tests use a real uvicorn server on an ephemeral loopback port so the
Streamable HTTP transport's chunked-body + POST-Accept semantics are exercised
the way Claude Desktop would exercise them.
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
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.annotations.types import AnnotationKind
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
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


def _sample_bar(day: int = 15) -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2024, 4, day, tzinfo=UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        source="yahoo",
    )


class _BarsProvider:
    """Stub provider that returns a pre-seeded list of bars on get_ohlcv."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars)
        self.calls: list[dict[str, object]] = []

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "as_of": as_of,
            },
        )
        return self.bars

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
        self, symbol: str, window: str, as_of: datetime | None = None
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


@pytest.fixture
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def provider() -> _BarsProvider:
    return _BarsProvider(bars=[_sample_bar()])


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def app(
    mcp_secret: str,
    provider: _BarsProvider,
    annotations_repo: AnnotationsRepository,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=provider,
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
    """Run the FastAPI app under uvicorn on an ephemeral loopback port."""
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
        streamable_http_client(
            f"{url}/mcp",
            http_client=http_client,
        ) as (read_stream, write_stream, _get_session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


def test_get_ohlcv_returns_at_least_one_bar(live_server: str, mcp_secret: str) -> None:
    """An MCP client with the MCP bearer can call get_ohlcv and receive bars."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "get_ohlcv",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "start": "2024-04-01T00:00:00+00:00",
                    "end": "2024-05-01T00:00:00+00:00",
                },
            )
            assert not result.isError, f"get_ohlcv errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    # FastMCP wraps list returns in `{"result": [...]}`.
    bars = payload.get("result", payload)
    assert isinstance(bars, list)
    assert len(bars) >= 1
    first = bars[0]
    assert isinstance(first, dict)
    assert first["symbol"] == "AAPL"
    assert first["timeframe"] == "1d"
    assert first["source"] == "yahoo"


def test_write_annotation_persists_and_lists_back(live_server: str, mcp_secret: str) -> None:
    """write_annotation → list_annotations sees the new row, including id+created_at."""

    async def _run() -> tuple[dict[str, object], list[dict[str, object]]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            write_result = await session.call_tool(
                "write_annotation",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "event_ts": "2026-04-15T00:00:00+00:00",
                    "kind": "bullish_marker",
                    "label": "hammer at support",
                    "agent_id": "claude-desktop-test",
                },
            )
            assert not write_result.isError, f"write errored: {write_result.content}"
            assert write_result.structuredContent is not None
            written = dict(write_result.structuredContent)

            list_result = await session.call_tool(
                "list_annotations",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "start": "2026-04-01T00:00:00+00:00",
                    "end": "2026-05-01T00:00:00+00:00",
                },
            )
            assert not list_result.isError, f"list errored: {list_result.content}"
            assert list_result.structuredContent is not None
            listed = list_result.structuredContent.get(
                "result",
                list_result.structuredContent,
            )
            assert isinstance(listed, list)
            return written, [dict(x) for x in listed]

    written, listed = asyncio.run(_run())

    assert written.get("id"), "write_annotation must return populated id"
    assert written.get("created_at"), "write_annotation must return populated created_at"
    assert written["symbol"] == "AAPL"
    assert written["kind"] == "bullish_marker"
    assert written["label"] == "hammer at support"
    assert written["agent_id"] == "claude-desktop-test"

    assert len(listed) == 1
    assert listed[0]["id"] == written["id"]


def test_write_annotation_visible_via_http_get_annotations(
    live_server: str, mcp_secret: str, client: TestClient
) -> None:
    """Cross-surface consistency: MCP-written annotation reads back via the renderer route."""

    async def _write() -> str:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "write_annotation",
                {
                    "symbol": "MSFT",
                    "timeframe": "1d",
                    "event_ts": "2026-04-15T00:00:00+00:00",
                    "kind": "bearish_marker",
                    "label": "engulfing top",
                },
            )
            assert not result.isError
            assert result.structuredContent is not None
            return str(result.structuredContent["id"])

    written_id = asyncio.run(_write())

    http_response = client.get(
        "/annotations",
        params={
            "symbol": "MSFT",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
        headers={"Authorization": f"Bearer {RENDERER_SECRET}"},
    )
    assert http_response.status_code == 200, http_response.text
    payload = http_response.json()
    assert isinstance(payload, list)
    assert any(row["id"] == written_id for row in payload), (
        f"MCP-written id {written_id} not found in HTTP response: {payload}"
    )


def test_write_annotation_invalid_kind_surfaces_mcp_error(
    live_server: str, mcp_secret: str
) -> None:
    """Bogus kind must error at the MCP boundary, not silently insert garbage."""

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            try:
                result = await session.call_tool(
                    "write_annotation",
                    {
                        "symbol": "AAPL",
                        "timeframe": "1d",
                        "event_ts": "2026-04-15T00:00:00+00:00",
                        "kind": "bogus",
                    },
                )
            except Exception:
                # MCP request-level error (Pydantic rejected the input before the body ran).
                return True
            # Or tool-level error response if FastMCP catches it after the body started.
            return bool(result.isError)

    errored = asyncio.run(_run())
    assert errored, "write_annotation(kind='bogus') must surface an MCP error"


def test_list_annotations_window_boundary_inclusive_via_mcp(
    live_server: str,
    mcp_secret: str,
    annotations_repo: AnnotationsRepository,
) -> None:
    """Same window semantics as the HTTP route: boundaries inclusive, outside excluded."""
    from market_analyser.annotations.types import Annotation

    for day in (10, 15, 20, 9, 21):
        annotations_repo.insert(
            Annotation(
                symbol="AAPL",
                timeframe="1d",
                event_ts=datetime(2026, 4, day, tzinfo=UTC),
                kind=AnnotationKind.BULLISH_MARKER,
            ),
        )

    async def _run() -> list[int]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "list_annotations",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "start": "2026-04-10T00:00:00+00:00",
                    "end": "2026-04-20T00:00:00+00:00",
                },
            )
            assert not result.isError
            assert result.structuredContent is not None
            rows = result.structuredContent.get("result", result.structuredContent)
            assert isinstance(rows, list)
            return [datetime.fromisoformat(r["event_ts"]).day for r in rows]

    days = sorted(asyncio.run(_run()))
    assert days == [10, 15, 20]


def test_ping_tool_no_longer_registered(live_server: str, mcp_secret: str) -> None:
    """Phase 1's walking-skeleton ping is gone; the three production tools are present."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    tool_names = asyncio.run(_run())
    assert "ping" not in tool_names
    assert {"get_ohlcv", "write_annotation", "list_annotations"} <= tool_names
