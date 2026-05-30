"""Plan 0007 phase 3 done-when: MCP `show_chart` / `update_chart` /
`highlight_pattern` tools.

Each tool:
- Validates inputs at the MCP boundary (Pydantic + explicit guards) — invalid
  shape surfaces as an MCP-level error to the agent, not a 500.
- Publishes exactly one envelope of the matching type.
- Returns `{event_published: True, type: ..., version: 1}` to the agent.

`highlight_pattern` additionally persists a row to the annotations table so
re-opening Electron after a session still shows the marker.

These tests reuse Plan 0006's `_mcp_session` pattern: real uvicorn on an
ephemeral port + MCP `streamable_http_client` + `ClientSession`. That way the
Streamable HTTP transport's full request/response shape is exercised the way
Claude Code would.
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
from market_analyser.api.events import (
    ChartHighlightPayloadV1,
    ChartShowPayloadV1,
    ChartUpdatePayloadV1,
    Envelope,
    EventBus,
)
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


class _FakeProvider:
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return []

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
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    event_bus: EventBus,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        event_bus=event_bus,
    )


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


def _drain_subscriber(event_bus: EventBus) -> list[Envelope]:
    """Subscribe BEFORE the test calls a tool, then drain the queue after.

    Because the bus is sync, publishing while a subscriber's queue has room
    enqueues immediately — there's no need to await. Returns whatever the
    subscriber's queue holds at call time."""
    sub = event_bus.subscribe()
    return sub, lambda: _drain_queue(sub)  # type: ignore[return-value]


def _drain_queue(sub: object) -> list[Envelope]:
    import asyncio as _asyncio

    items: list[Envelope] = []
    queue = sub.queue  # type: ignore[attr-defined]
    while True:
        try:
            items.append(queue.get_nowait())
        except _asyncio.QueueEmpty:
            break
    return items


# --------------------------------------------------------------------------- #
# show_chart                                                                  #
# --------------------------------------------------------------------------- #


def test_show_chart_publishes_chart_show_v1(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """Subscribe to the bus from the test thread, call `show_chart` via MCP,
    then drain. Exactly one `chart.show v1` envelope with payload that
    matches the supplied args (modulo serialization)."""
    sub = event_bus.subscribe()

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "show_chart",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "range_start": "2026-04-20T00:00:00+00:00",
                    "range_end": "2026-05-20T00:00:00+00:00",
                    "overlays": [{"kind": "ema", "period": 20}],
                },
            )
            assert not result.isError, f"show_chart errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    ack = asyncio.run(_run())
    assert ack == {
        "event_published": True,
        "type": "chart.show",
        "version": ChartShowPayloadV1.VERSION,
    }

    queued = _drain_queue(sub)
    assert len(queued) == 1, f"expected exactly one envelope, got {len(queued)}"
    env = queued[0]
    assert env.type == "chart.show"
    assert env.version == 1
    payload = env.payload
    assert payload["symbol"] == "AAPL"
    assert payload["timeframe"] == "1d"
    assert payload["range_start"].startswith("2026-04-20")
    assert payload["range_end"].startswith("2026-05-20")
    assert payload["overlays"] == [{"kind": "ema", "period": 20}]


# --------------------------------------------------------------------------- #
# update_chart                                                                #
# --------------------------------------------------------------------------- #


def test_update_chart_omits_unset_fields_from_payload(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """No range/focus fields supplied → payload contains only `symbol`,
    `timeframe`, and `overlays` (no nulls). Done-when explicitly disallows
    `range_start: null` / `range_end: null` on the wire."""
    sub = event_bus.subscribe()

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "update_chart",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "overlays": [{"kind": "ema", "period": 50}],
                },
            )
            assert not result.isError, f"update_chart errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    ack = asyncio.run(_run())
    assert ack == {
        "event_published": True,
        "type": "chart.update",
        "version": ChartUpdatePayloadV1.VERSION,
    }

    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.update"
    payload_keys = set(env.payload.keys())
    assert payload_keys == {"symbol", "timeframe", "overlays"}, (
        f"unset fields should not appear; got keys={payload_keys}"
    )


def test_update_chart_carries_supplied_range(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """Sanity check the opposite direction: supplied range fields DO appear."""
    sub = event_bus.subscribe()

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "update_chart",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "range_start": "2026-05-10T00:00:00+00:00",
                    "range_end": "2026-05-20T00:00:00+00:00",
                },
            )
            assert not result.isError

    asyncio.run(_run())
    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.update"
    assert "range_start" in env.payload
    assert "range_end" in env.payload
    assert env.payload["range_start"].startswith("2026-05-10")
    assert env.payload["range_end"].startswith("2026-05-20")


# --------------------------------------------------------------------------- #
# highlight_pattern                                                           #
# --------------------------------------------------------------------------- #


def test_highlight_pattern_publishes_event_and_persists_annotation(
    live_server: str,
    mcp_secret: str,
    event_bus: EventBus,
    annotations_repo: AnnotationsRepository,
) -> None:
    """`highlight_pattern` does BOTH: publishes one `chart.highlight v1`
    envelope AND inserts an annotations row visible via
    `AnnotationsRepository.list_for(...)`."""
    sub = event_bus.subscribe()

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "highlight_pattern",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "event_ts": "2026-05-15T00:00:00+00:00",
                    "kind": "bullish_marker",
                    "label": "hammer at support",
                },
            )
            assert not result.isError, f"highlight_pattern errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    ack = asyncio.run(_run())
    assert ack == {
        "event_published": True,
        "type": "chart.highlight",
        "version": ChartHighlightPayloadV1.VERSION,
    }

    # Live event published.
    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.highlight"
    assert env.payload["symbol"] == "AAPL"
    assert env.payload["timeframe"] == "1d"
    assert len(env.payload["markers"]) == 1
    marker = env.payload["markers"][0]
    assert marker["kind"] == "bullish_marker"
    assert marker["label"] == "hammer at support"
    assert marker["event_ts"].startswith("2026-05-15")

    # Annotation persisted.
    listed = annotations_repo.list_for(
        symbol="AAPL",
        timeframe="1d",
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 31, tzinfo=UTC),
    )
    assert len(listed) == 1
    assert listed[0].kind == "bullish_marker"
    assert listed[0].label == "hammer at support"


# --------------------------------------------------------------------------- #
# Input validation — each invalid input must surface as an MCP error          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        # show_chart rejections
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "5m",  # not in SUPPORTED_TIMEFRAMES
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
            },
        ),
        (
            "show_chart",
            {
                "symbol": "",  # empty
                "timeframe": "1d",
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
            },
        ),
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": "2026-05-20T00:00:00+00:00",  # > range_end
                "range_end": "2026-04-20T00:00:00+00:00",
            },
        ),
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
                "overlays": [{"kind": "unknown"}],  # not in literal set
            },
        ),
        # update_chart rejections
        (
            "update_chart",
            {"symbol": "AAPL", "timeframe": "5m"},
        ),
        (
            "update_chart",
            {"symbol": "", "timeframe": "1d"},
        ),
        (
            "update_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "overlays": [{"kind": "unknown"}],
            },
        ),
        # highlight_pattern rejections
        (
            "highlight_pattern",
            {
                "symbol": "AAPL",
                "timeframe": "5m",
                "event_ts": "2026-05-15T00:00:00+00:00",
                "kind": "bullish_marker",
            },
        ),
        (
            "highlight_pattern",
            {
                "symbol": "",
                "timeframe": "1d",
                "event_ts": "2026-05-15T00:00:00+00:00",
                "kind": "bullish_marker",
            },
        ),
    ],
)
def test_tool_rejects_invalid_input_with_mcp_error(
    live_server: str, mcp_secret: str, tool: str, args: dict[str, object]
) -> None:
    """Each invalid input must surface as an MCP-level error (isError=True),
    not as a 500. The agent should see a graceful rejection."""

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(tool, args)
            return result.isError

    is_error = asyncio.run(_run())
    assert is_error, f"{tool}({args}) should have surfaced an MCP error"


# --------------------------------------------------------------------------- #
# Plan 0025 ph3: the widened timeframe set propagates to the chart validators  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("timeframe", ["15m", "4h", "1w"])
@pytest.mark.parametrize("tool", ["show_chart", "update_chart"])
def test_chart_tools_accept_new_timeframes(
    live_server: str, mcp_secret: str, tool: str, timeframe: str
) -> None:
    """show_chart / update_chart validate via the shared `_require_supported_timeframe`,
    so widening SUPPORTED_TIMEFRAMES must make them accept 15m / 4h / 1w. Pinned
    here rather than trusting the shared import (Plan 0025 ph3 done-when)."""
    args: dict[str, object] = {"symbol": "AAPL", "timeframe": timeframe}
    if tool == "show_chart":
        args |= {
            "range_start": "2026-04-20T00:00:00+00:00",
            "range_end": "2026-05-20T00:00:00+00:00",
        }

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(tool, args)
            return result.isError

    assert asyncio.run(_run()) is False, f"{tool} should accept timeframe {timeframe}"


# --------------------------------------------------------------------------- #
# Regression: pre-existing Plan-0006 tools still work                         #
# --------------------------------------------------------------------------- #


def test_get_ohlcv_still_reachable_after_show_tools_landed(
    live_server: str, mcp_secret: str
) -> None:
    """Smoke regression: the three tools added in this phase coexist with
    Plan 0006's tools. The full Plan-0006 suite runs as part of the broader
    `tests/api/test_mcp_tools.py` file — this is a one-line probe."""

    async def _run() -> bool:
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
            return not result.isError

    assert asyncio.run(_run()) is True
