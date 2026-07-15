"""Plan 0104 phase 2: the user-drawing read-back surface (ADR-0099).

Covers the write half (`PUT /user_drawings/{symbol}` — renderer-bearer, declarative
replace, user-provenance only), the read half (`get_chart_drawings` MCP tool — the
mirrored set + honest `synced_at`), the `ui.drawing_changed v1` event (buffered with
no mode precondition, ADR-0101), and the ADR-0014 dual-bearer split (the MCP bearer
cannot PUT; the renderer bearer cannot reach the MCP tool).

Live-server tests reuse Plan 0006's `_mcp_session` harness (real uvicorn +
Streamable HTTP client), the same shape as `test_ui_events_mcp.py`.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
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

_T0 = "2026-04-25T00:00:00+00:00"
_T1 = "2026-05-05T00:00:00+00:00"


def _pt(ts: str, price: float) -> dict[str, Any]:
    return {"ts": ts, "price": price}


def _user(kind: str, points: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "points": points, "provenance": "user", **extra}


# One valid user-provenance drawing per kind — the full eleven-kind vocabulary.
def _all_eleven_kinds() -> list[dict[str, Any]]:
    two = [_pt(_T0, 100.0), _pt(_T1, 110.0)]
    one = [_pt(_T0, 100.0)]
    return [
        _user("trendline", two, id="k-trendline"),
        _user("ray", two, id="k-ray"),
        _user("hline", one, id="k-hline"),
        _user("vline", one, id="k-vline"),
        _user("rect", two, id="k-rect"),
        _user("fib", two, id="k-fib"),
        _user("long_position", [_pt(_T0, 100.0)], stop=95.0, target=115.0, id="k-long"),
        _user("short_position", [_pt(_T0, 100.0)], stop=110.0, target=90.0, id="k-short"),
        _user("date_range", two, id="k-date"),
        _user("price_range", two, id="k-price"),
        _user("date_price_range", two, id="k-dateprice"),
    ]


class _FakeProvider:
    """The drawing routes/tools never touch the data path; bodies never run."""

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
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


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


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


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


# --------------------------------------------------------------------------- #
# PUT /user_drawings/{symbol}  ⇄  get_chart_drawings  (round-trip)             #
# --------------------------------------------------------------------------- #


def test_put_then_get_round_trips_all_eleven_kinds(live_server: str, mcp_secret: str) -> None:
    """A renderer PUT of the full eleven-kind user set is read back verbatim by
    the agent's `get_chart_drawings`, with a real `synced_at`."""
    put = httpx.put(
        f"{live_server}/user_drawings/BTC-USD",
        json=_all_eleven_kinds(),
        headers=_renderer_headers(),
        timeout=10.0,
    )
    assert put.status_code == 200, put.text
    assert put.json()["drawing_count"] == 11

    async def _run() -> dict[str, Any]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_chart_drawings", {"symbol": "BTC-USD"})
            assert not result.isError, result.content
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    got = asyncio.run(_run())
    assert got["symbol"] == "BTC-USD"
    assert [d["kind"] for d in got["drawings"]] == [d["kind"] for d in _all_eleven_kinds()]
    assert all(d["provenance"] == "user" for d in got["drawings"])
    # A long position round-trips its stop/target; risk-reward is never on the wire.
    long_box = next(d for d in got["drawings"] if d["kind"] == "long_position")
    assert long_box["stop"] == 95.0 and long_box["target"] == 115.0
    assert got["synced_at"] is not None


def test_second_put_replaces_not_appends(client: TestClient, app: FastAPI) -> None:
    """The mirror is a declarative replace: the second PUT's set fully supplants
    the first, never appends to it."""
    first = client.put(
        "/user_drawings/AAPL",
        json=[
            _user("hline", [_pt(_T0, 200.0)], id="a"),
            _user("hline", [_pt(_T0, 210.0)], id="b"),
        ],
        headers=_renderer_headers(),
    )
    assert first.status_code == 200, first.text
    second = client.put(
        "/user_drawings/AAPL",
        json=[_user("vline", [_pt(_T1, 205.0)], id="c")],
        headers=_renderer_headers(),
    )
    assert second.status_code == 200, second.text

    snapshot = app.state.user_drawings_mirror.snapshot("AAPL")
    assert [d.id for d in snapshot.drawings] == ["c"]


def test_put_rejects_agent_provenance(client: TestClient, app: FastAPI) -> None:
    """The mirror is user-drawings-only: an `agent`-provenance spec is rejected
    422 and nothing is stored (the inverse of the agent-only chart.annotations)."""
    response = client.put(
        "/user_drawings/AAPL",
        json=[{"kind": "hline", "points": [_pt(_T0, 200.0)], "provenance": "agent", "id": "x"}],
        headers=_renderer_headers(),
    )
    assert response.status_code == 422
    assert app.state.user_drawings_mirror.snapshot("AAPL").synced_at is None


def test_put_rejects_malformed_position(client: TestClient) -> None:
    """A long_position with a target below entry violates the model invariant and
    is rejected 422 by DrawingSpec before it reaches the mirror."""
    response = client.put(
        "/user_drawings/AAPL",
        json=[_user("long_position", [_pt(_T0, 100.0)], stop=95.0, target=90.0, id="bad")],
        headers=_renderer_headers(),
    )
    assert response.status_code == 422


def test_get_chart_drawings_synced_at_null_before_sync(live_server: str, mcp_secret: str) -> None:
    """Never-synced is honestly distinct from empty: a symbol with no sync yet
    returns an empty set with `synced_at=null` (ADR-0099 staleness signal)."""

    async def _run() -> dict[str, Any]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_chart_drawings", {"symbol": "NEVER-SYNCED"})
            assert not result.isError, result.content
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    got = asyncio.run(_run())
    assert got["drawings"] == []
    assert got["synced_at"] is None


# --------------------------------------------------------------------------- #
# ui.drawing_changed v1 — buffered with no mode precondition (ADR-0101)        #
# --------------------------------------------------------------------------- #


def test_ui_drawing_changed_buffers_with_no_mode_precondition(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/ui_events",
        json={
            "type": "ui.drawing_changed",
            "version": 1,
            "payload": {
                "symbol": "BTC-USD",
                "change": "created",
                "drawing_id": "abc",
                "kind": "long_position",
            },
        },
        headers=_renderer_headers(),
    )
    assert response.status_code == 202, response.text
    snap = app.state.ui_event_buffer.snapshot()
    assert len(snap) == 1
    assert snap[0].type == "ui.drawing_changed"
    assert snap[0].payload["change"] == "created"
    assert snap[0].payload["drawing_id"] == "abc"


def test_ui_drawing_changed_rejects_bad_change(client: TestClient, app: FastAPI) -> None:
    """`change` is a closed set — an unknown value is a 422 at the boundary."""
    response = client.post(
        "/ui_events",
        json={
            "type": "ui.drawing_changed",
            "version": 1,
            "payload": {
                "symbol": "BTC-USD",
                "change": "renamed",  # not in created|modified|deleted
                "drawing_id": "abc",
                "kind": "hline",
            },
        },
        headers=_renderer_headers(),
    )
    assert response.status_code == 422
    assert app.state.ui_event_buffer.snapshot() == []


# --------------------------------------------------------------------------- #
# ADR-0014 dual-bearer split                                                   #
# --------------------------------------------------------------------------- #


def test_put_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: the MCP bearer cannot PUT to the renderer route."""
    response = client.put(
        "/user_drawings/AAPL",
        json=[_user("hline", [_pt(_T0, 200.0)], id="x")],
        headers=_mcp_headers(mcp_secret),
    )
    assert response.status_code == 401


def test_mcp_surface_rejects_renderer_bearer(live_server: str) -> None:
    """Cross-tenant: the renderer bearer cannot reach the MCP surface (and thus
    the `get_chart_drawings` tool) — the central middleware 401s it at `/mcp`."""
    response = httpx.post(
        f"{live_server}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**_renderer_headers(), "Accept": "application/json, text/event-stream"},
        timeout=10.0,
    )
    assert response.status_code == 401
