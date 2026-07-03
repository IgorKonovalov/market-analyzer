"""Plan 0060 phase 3 — the watch-management MCP toolset.

Done-when claims pinned here:
(b) the full-toolset registration grows the four new tools (`create_watch`,
    `list_watches`, `delete_watch`, `list_alerts`) — asserted against a live
    Streamable-HTTP server, exactly how an MCP client sees it;
plus the tool-body contracts: `create_watch`'s deep boundary (strategy
resolution at creation, bar-period interval default), `list_alerts`' honest
ADR-0046 paging, and `/healthz` exposing the scheduler heartbeat.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.alerts.types import UnknownWatchKindError
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.watches import (
    MAX_ALERTS,
    _create_watch_response,
    _list_alerts_response,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

RENDERER_SECRET = "renderer-test-secret"
CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
FIRED_AT = datetime(2026, 7, 2, tzinfo=UTC)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def watches(session_factory: sessionmaker[Session]) -> WatchesRepository:
    return WatchesRepository(session_factory)


@pytest.fixture
def alerts(session_factory: sessionmaker[Session]) -> AlertsRepository:
    return AlertsRepository(session_factory)


class TestCreateWatchBody:
    def test_interval_defaults_to_bar_period(self, watches: WatchesRepository) -> None:
        watch = _create_watch_response(
            watches_repository=watches,
            symbol="BTC-USD",
            timeframe="1d",
            kind="indicator_threshold",
            params={"indicator": "rsi", "operator": "<", "level": 30.0},
            interval_seconds=None,
            enabled=True,
            now=CREATED_AT,
        )
        assert watch.interval_seconds == int(timedelta(days=1).total_seconds())
        assert watch.enabled is True
        assert watch.last_state is None

    def test_explicit_interval_is_respected(self, watches: WatchesRepository) -> None:
        watch = _create_watch_response(
            watches_repository=watches,
            symbol="BTC-USD",
            timeframe="1d",
            kind="pattern",
            params={"pattern": "hammer"},
            interval_seconds=3600,
            enabled=False,
            now=CREATED_AT,
        )
        assert watch.interval_seconds == 3600
        assert watch.enabled is False

    def test_strategy_watch_resolves_strategy_at_creation(self, watches: WatchesRepository) -> None:
        watch = _create_watch_response(
            watches_repository=watches,
            symbol="BTC-USD",
            timeframe="1d",
            kind="strategy_signal",
            params={"strategy_id": "rsi", "params": {}},
            interval_seconds=None,
            enabled=True,
            now=CREATED_AT,
        )
        assert watch.kind == "strategy_signal"

    def test_unknown_strategy_is_refused_at_creation(self, watches: WatchesRepository) -> None:
        with pytest.raises(ValueError, match="unknown strategy_id"):
            _create_watch_response(
                watches_repository=watches,
                symbol="BTC-USD",
                timeframe="1d",
                kind="strategy_signal",
                params={"strategy_id": "gone_strategy", "params": {}},
                interval_seconds=None,
                enabled=True,
                now=CREATED_AT,
            )
        assert watches.list() == []

    def test_strategy_unsupported_timeframe_is_refused(self, watches: WatchesRepository) -> None:
        # The `rsi` strategy supports ("1h", "1d") — "1wk" is registry-valid
        # but outside the strategy's declared set.
        with pytest.raises(ValueError, match="not supported by strategy"):
            _create_watch_response(
                watches_repository=watches,
                symbol="BTC-USD",
                timeframe="1wk",
                kind="strategy_signal",
                params={"strategy_id": "rsi", "params": {}},
                interval_seconds=None,
                enabled=True,
                now=CREATED_AT,
            )

    def test_bad_strategy_params_are_refused(self, watches: WatchesRepository) -> None:
        with pytest.raises(ValidationError):
            _create_watch_response(
                watches_repository=watches,
                symbol="BTC-USD",
                timeframe="1d",
                kind="strategy_signal",
                params={"strategy_id": "rsi", "params": {"not_a_param": 1}},
                interval_seconds=None,
                enabled=True,
                now=CREATED_AT,
            )

    def test_unknown_kind_is_refused(self, watches: WatchesRepository) -> None:
        with pytest.raises(UnknownWatchKindError):
            _create_watch_response(
                watches_repository=watches,
                symbol="BTC-USD",
                timeframe="1d",
                kind="forecast_probability",
                params={},
                interval_seconds=None,
                enabled=True,
                now=CREATED_AT,
            )


class TestListAlertsBody:
    def _seed(self, watches: WatchesRepository, alerts: AlertsRepository, count: int) -> int:
        watch = watches.create(
            symbol="BTC-USD",
            timeframe="1d",
            kind="pattern",
            params={"pattern": "doji"},
            interval_seconds=86_400,
            created_at=CREATED_AT,
        )
        for i in range(count):
            alerts.insert(
                watch_id=watch.id,
                fired_at=FIRED_AT + timedelta(hours=i),
                payload={"condition": f"fact {i}", "values": {}},
            )
        return watch.id

    def test_pages_newest_first_with_honest_envelope(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        self._seed(watches, alerts, 5)
        first = _list_alerts_response(
            alerts_repository=alerts, watch_id=None, offset=0, max_alerts=2
        )
        assert first.total_available == 5
        assert first.returned == 2
        assert first.partial_reason == "too_large"
        assert first.message is not None and "offset=2" in first.message
        assert [a.payload["condition"] for a in first.alerts] == ["fact 4", "fact 3"]

        last = _list_alerts_response(
            alerts_repository=alerts, watch_id=None, offset=4, max_alerts=2
        )
        assert last.returned == 1
        assert last.partial_reason is None
        assert last.message is None
        assert [a.payload["condition"] for a in last.alerts] == ["fact 0"]

    def test_watch_id_scopes_and_page_size_is_capped(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        watch_id = self._seed(watches, alerts, 3)
        scoped = _list_alerts_response(
            alerts_repository=alerts, watch_id=watch_id, offset=0, max_alerts=None
        )
        assert scoped.total_available == 3
        assert scoped.returned == 3

        oversized = _list_alerts_response(
            alerts_repository=alerts, watch_id=None, offset=0, max_alerts=MAX_ALERTS * 10
        )
        assert oversized.returned == 3  # capped page size still serves all 3

    def test_bounds_are_validated(self, alerts: AlertsRepository) -> None:
        with pytest.raises(ValueError, match="offset"):
            _list_alerts_response(
                alerts_repository=alerts, watch_id=None, offset=-1, max_alerts=None
            )
        with pytest.raises(ValueError, match="max_alerts"):
            _list_alerts_response(alerts_repository=alerts, watch_id=None, offset=0, max_alerts=0)


# --- Live-server MCP surface -------------------------------------------------- #


class _EmptyProvider:
    """get_ohlcv-only double: the scheduler's background ticks (and any tool
    fetch) see an empty cache — evaluations are False, nothing fires, so the
    MCP round-trips below stay deterministic regardless of timing."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[object]:
        return []


@pytest.fixture
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine(":memory:")
    yield engine
    engine.dispose()


@pytest.fixture
def app(mcp_secret: str, engine: Engine, tmp_path: Path) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        engine=engine,
        provider=_EmptyProvider(),  # type: ignore[arg-type]  # get_ohlcv is all this test exercises
        agent_mode_path=tmp_path / "agent_mode.json",
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


def _result_list(structured: dict[str, object] | None) -> list[dict[str, object]]:
    """FastMCP wraps a list return under `result`; unwrap defensively."""
    assert structured is not None
    result = structured.get("result", structured)
    assert isinstance(result, list)
    return [dict(item) for item in result]


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


def test_watch_toolset_is_registered(live_server: str, mcp_secret: str) -> None:
    """Plan 0060 phase 3 done-when (b): the live toolset grows the four watch
    tools when the alerting repositories are wired."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    tool_names = asyncio.run(_run())
    assert {"create_watch", "list_watches", "delete_watch", "list_alerts"} <= tool_names


def test_watch_lifecycle_round_trips_over_mcp(live_server: str, mcp_secret: str) -> None:
    """create -> list -> delete -> list, all through the live MCP surface."""

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            created = await session.call_tool(
                "create_watch",
                {
                    "symbol": "BTC-USD",
                    "timeframe": "1d",
                    "kind": "indicator_threshold",
                    "params": {"indicator": "rsi", "operator": "<", "level": 30.0},
                },
            )
            assert created.isError is False
            assert created.structuredContent is not None
            watch_id = created.structuredContent["id"]
            assert created.structuredContent["interval_seconds"] == 86_400

            listed = await session.call_tool("list_watches", {})
            assert listed.isError is False
            ids = [w["id"] for w in _result_list(listed.structuredContent)]
            assert watch_id in ids

            empty_history = await session.call_tool("list_alerts", {"watch_id": watch_id})
            assert empty_history.isError is False
            assert empty_history.structuredContent is not None
            assert empty_history.structuredContent["total_available"] == 0

            deleted = await session.call_tool("delete_watch", {"watch_id": watch_id})
            assert deleted.isError is False
            assert deleted.structuredContent == {"deleted": True}

            relisted = await session.call_tool("list_watches", {})
            assert relisted.isError is False
            assert _result_list(relisted.structuredContent) == []

    asyncio.run(_run())


def test_create_watch_with_bogus_kind_errors_over_mcp(live_server: str, mcp_secret: str) -> None:
    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "create_watch",
                {
                    "symbol": "BTC-USD",
                    "timeframe": "1d",
                    "kind": "forecast_probability",
                    "params": {},
                },
            )
            assert result.isError is True

    asyncio.run(_run())


def test_healthz_exposes_scheduler_heartbeat(app: FastAPI) -> None:
    """The heartbeat rides the existing health surface: with persistence
    wired the scheduler exists, and /healthz reports its liveness fields
    (no ticks yet — the poll loop's first sleep is longer than this test)."""
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["ok"] is True
    heartbeat = body["alert_scheduler"]
    assert heartbeat["last_tick_at"] is None
    assert heartbeat["tick_count"] == 0
    assert heartbeat["watch_errors"] == {}
    assert heartbeat["last_tick_error"] is None
