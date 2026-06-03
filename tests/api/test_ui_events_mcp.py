"""Plan 0014 phase 2 done-when: the agent-facing MCP surface for UI events.

Covers the `get_pending_ui_events` tool (drain/peek/since semantics + docstring),
the `ui-events://recent` resource (listed + non-draining read), and the
`on_append` → resource-update notification seam (fires once per append; no-ops
gracefully with no active MCP session — the stateless_http transport reality
ADR-0021 flagged). The reliable contract is the tool; the notification is the
best-effort nudge.

Live-server tests reuse Plan 0006's `_mcp_session` harness (real uvicorn +
Streamable HTTP client) so the transport is exercised the way Claude Code would.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from market_analyser.api.app import create_app
from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.get_pending_ui_events import (
    UI_EVENTS_RESOURCE_URI,
    _ResourceUpdateNotifier,
)
from market_analyser.api.ui_events import UIEventEnvelope
from market_analyser.api.ui_events.buffer import UIEventBuffer
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
from market_analyser.events import EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _envelope(n: int) -> UIEventEnvelope:
    """A distinct, ordered envelope: ts = base + n minutes, unique uuid4 id."""
    return UIEventEnvelope(
        event_id=str(uuid.uuid4()),
        type="ui.bar_clicked",
        version=1,
        ts=_BASE + timedelta(minutes=n),
        payload={"symbol": "AAPL", "timeframe": "1d", "n": n},
    )


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
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    tmp_path: Path,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        agent_mode_path=tmp_path / "agent_mode.json",
    )


@pytest.fixture
def buffer(app: FastAPI) -> UIEventBuffer:
    """The app's UI-event buffer — shared with the in-thread uvicorn server, so
    the test can pre-load events the MCP tool then reads/drains."""
    return app.state.ui_event_buffer  # type: ignore[no-any-return]


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
        streamable_http_client(f"{url}/mcp", http_client=http_client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


def _result_list(structured: dict[str, Any] | None) -> list[dict[str, Any]]:
    """FastMCP wraps a list return under `result`; unwrap defensively."""
    assert structured is not None
    result = structured.get("result", structured)
    return list(result)


# --------------------------------------------------------------------------- #
# get_pending_ui_events tool                                                  #
# --------------------------------------------------------------------------- #


def test_get_pending_ui_events_empty_returns_empty(live_server: str, mcp_secret: str) -> None:
    async def _run() -> list[dict[str, Any]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_pending_ui_events", {})
            assert not result.isError, result.content
            return _result_list(result.structuredContent)

    assert asyncio.run(_run()) == []


def test_get_pending_ui_events_returns_all_in_order_and_drains(
    live_server: str, mcp_secret: str, buffer: UIEventBuffer
) -> None:
    envelopes = [_envelope(i) for i in range(1, 4)]
    for env in envelopes:
        buffer.append(env)

    async def _run() -> list[dict[str, Any]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_pending_ui_events", {})
            assert not result.isError, result.content
            return _result_list(result.structuredContent)

    returned = asyncio.run(_run())
    assert [e["event_id"] for e in returned] == [e.event_id for e in envelopes]
    for got, expected in zip(returned, envelopes, strict=True):
        assert got["type"] == expected.type
        assert got["version"] == expected.version
        assert got["ts"].startswith("2026-05-22")
        assert got["payload"]["n"] == expected.payload["n"]
    # Default drain=True empties the buffer.
    assert buffer.snapshot() == []


def test_get_pending_ui_events_drain_false_peeks(
    live_server: str, mcp_secret: str, buffer: UIEventBuffer
) -> None:
    envelopes = [_envelope(i) for i in range(1, 4)]
    for env in envelopes:
        buffer.append(env)

    async def _run() -> list[dict[str, Any]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_pending_ui_events", {"drain": False})
            assert not result.isError, result.content
            return _result_list(result.structuredContent)

    returned = asyncio.run(_run())
    assert len(returned) == 3
    # Buffer untouched by a peek.
    assert len(buffer.snapshot()) == 3


def test_get_pending_ui_events_since_is_strict_greater_than(
    live_server: str, mcp_secret: str, buffer: UIEventBuffer
) -> None:
    envelopes = [_envelope(i) for i in range(1, 4)]  # ts +1,+2,+3 min
    for env in envelopes:
        buffer.append(env)
    since_iso = envelopes[1].ts.isoformat()  # ts of envelope 2

    async def _run() -> list[dict[str, Any]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_pending_ui_events", {"since": since_iso})
            assert not result.isError, result.content
            return _result_list(result.structuredContent)

    returned = asyncio.run(_run())
    # Strict > since → only envelope 3.
    assert [e["payload"]["n"] for e in returned] == [3]
    # drain=True consumed envelope 3; 1 and 2 remain.
    assert [e.payload["n"] for e in buffer.snapshot()] == [1, 2]


def test_tool_description_sets_agent_mental_model(live_server: str, mcp_secret: str) -> None:
    async def _run() -> str:
        async with _mcp_session(live_server, mcp_secret) as session:
            tools = await session.list_tools()
            tool = next(t for t in tools.tools if t.name == "get_pending_ui_events")
            return tool.description or ""

    description = asyncio.run(_run())
    assert "agent mode" in description
    assert "draining" in description
    assert UI_EVENTS_RESOURCE_URI in description


# --------------------------------------------------------------------------- #
# ui-events://recent resource                                                 #
# --------------------------------------------------------------------------- #


def test_resource_listed_with_description(live_server: str, mcp_secret: str) -> None:
    async def _run() -> tuple[list[str], str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            listed = await session.list_resources()
            uris = [str(r.uri) for r in listed.resources]
            descs = {str(r.uri): (r.description or "") for r in listed.resources}
            return uris, descs.get(UI_EVENTS_RESOURCE_URI, "")

    uris, description = asyncio.run(_run())
    assert UI_EVENTS_RESOURCE_URI in uris
    assert description != ""


def test_resource_read_is_non_draining(
    live_server: str, mcp_secret: str, buffer: UIEventBuffer
) -> None:
    envelopes = [_envelope(i) for i in range(1, 3)]
    for env in envelopes:
        buffer.append(env)

    async def _run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            first = await session.read_resource(AnyUrl(UI_EVENTS_RESOURCE_URI))
            second = await session.read_resource(AnyUrl(UI_EVENTS_RESOURCE_URI))
            return (
                json.loads(first.contents[0].text),  # type: ignore[union-attr]
                json.loads(second.contents[0].text),  # type: ignore[union-attr]
            )

    first, second = asyncio.run(_run())
    assert [e["event_id"] for e in first] == [e.event_id for e in envelopes]
    # Two consecutive reads return the same data — the read does not drain.
    assert first == second
    assert len(buffer.snapshot()) == 2


# --------------------------------------------------------------------------- #
# resource-update notification seam                                           #
# --------------------------------------------------------------------------- #


def test_notifier_fires_once_per_append_with_uri() -> None:
    calls: list[str] = []
    buf = UIEventBuffer()
    buf.on_append(_ResourceUpdateNotifier(calls.append, UI_EVENTS_RESOURCE_URI))

    buf.append(_envelope(1))
    buf.append(_envelope(2))

    # Once per append, each with the resource URI — multiple appends, multiple
    # notifications.
    assert calls == [UI_EVENTS_RESOURCE_URI, UI_EVENTS_RESOURCE_URI]


def test_append_with_no_mcp_session_does_not_raise(
    annotations_repo: AnnotationsRepository,
) -> None:
    """The production notifier wired by create_mcp_components must no-op (not
    raise) when no MCP session/loop is active — the usual case for a
    renderer-driven append."""
    buf = UIEventBuffer()
    create_mcp_components(
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        event_bus=EventBus(),
        ui_event_buffer=buf,
    )
    # Must not raise even though no MCP client is connected.
    buf.append(_envelope(1))
    assert len(buf.snapshot()) == 1


# --------------------------------------------------------------------------- #
# Regression: pre-existing tools coexist                                      #
# --------------------------------------------------------------------------- #


def test_existing_tools_still_present(live_server: str, mcp_secret: str) -> None:
    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    names = asyncio.run(_run())
    assert {
        "get_ohlcv",
        "write_annotation",
        "list_annotations",
        "show_chart",
        "update_chart",
        "highlight_pattern",
        "get_pending_ui_events",
    } <= names
