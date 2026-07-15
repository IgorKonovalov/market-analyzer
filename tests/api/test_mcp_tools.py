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


def test_get_ohlcv_returns_structured_response_with_bars(live_server: str, mcp_secret: str) -> None:
    """get_ohlcv returns the Plan 0013 `{bars, partial_reason, message}` shape
    (no longer a bare list); bars match the cached set, partial_reason is null."""

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
    assert payload["partial_reason"] is None
    assert payload["message"] is None
    bars = payload["bars"]
    assert isinstance(bars, list)
    assert len(bars) >= 1
    first = bars[0]
    assert isinstance(first, dict)
    assert first["symbol"] == "AAPL"
    assert first["timeframe"] == "1d"
    assert first["source"] == "yahoo"


def test_get_ohlcv_description_advertises_fetch_on_miss(live_server: str, mcp_secret: str) -> None:
    """The agent reads the tool description to decide whether get_ohlcv can
    populate the cache (ADR-0015). Plan 0013 done-when: it must NOT say "from the
    local cache" (the wording that made the agent treat it as cache-only) and
    MUST mention fetching + a cache miss."""

    async def _run() -> str:
        async with _mcp_session(live_server, mcp_secret) as session:
            tools = await session.list_tools()
            tool = next(t for t in tools.tools if t.name == "get_ohlcv")
            return tool.description or ""

    description = asyncio.run(_run())
    assert "from the local cache" not in description
    assert "fetch" in description
    assert "miss" in description


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


def test_scan_patterns_tool_is_registered(live_server: str, mcp_secret: str) -> None:
    """`scan_patterns` (Plan 0049) is wired in `create_mcp_components`; a forgotten
    registration would drop it from the live toolset and fail here."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    assert "scan_patterns" in asyncio.run(_run())


def test_forecast_tool_is_registered(live_server: str, mcp_secret: str) -> None:
    """`forecast` (Plan 0036) is always registered in `create_mcp_components`
    (needs only the provider); a forgotten registration would drop it here."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    assert "forecast" in asyncio.run(_run())


def test_recommend_tool_is_registered(live_server: str, mcp_secret: str) -> None:
    """`recommend` (Plan 0038) is always registered in `create_mcp_components`
    (needs only the provider + optional coordinator/models_dir); a forgotten
    registration would drop the advisor surface from the live toolset here."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    assert "recommend" in asyncio.run(_run())


def test_detect_levels_tool_is_registered(live_server: str, mcp_secret: str) -> None:
    """`detect_levels` (Plan 0051) is wired in `create_mcp_components` (needs only
    the always-present provider + event bus); a forgotten registration would drop
    it from the live toolset and fail here."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    assert "detect_levels" in asyncio.run(_run())


def test_detect_chart_patterns_tool_is_registered(live_server: str, mcp_secret: str) -> None:
    """`detect_chart_patterns` (Plan 0052) is wired in `create_mcp_components`
    (needs only the always-present provider + event bus); a forgotten
    registration would drop it from the live toolset and fail here."""

    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.list_tools()
            return {t.name for t in result.tools}

    assert "detect_chart_patterns" in asyncio.run(_run())


# --- exhaustive toolset registration (Plan 0035 phase 7) --------------------------
#
# The safety net the 2026-05-31 audit asked for ("no safety-net test that every
# MCP tool is registered"): build `create_mcp_components` with EVERY dependency
# wired and assert the toolset is exactly the expected set, by name. A forgotten
# `register_*` call fails here; so does a new tool that forgets to update this
# roster (deliberate — the roster is the reviewable record of the agent surface).

EXPECTED_FULL_TOOLSET = {
    "analyze_symbol",
    "annotate_chart",
    "backfill_ohlcv",
    "bitcoin_market_pulse",
    "btc_cycle_snapshot",
    "compare_strategies",
    "compute_wallet_pnl",
    "create_watch",
    "crypto_fear_greed",
    "delete_watch",
    "derivatives_snapshot",
    "detect_chart_patterns",
    "detect_divergences",
    "detect_levels",
    "evaluate_signals",
    "find_convergence_opportunities",
    "forecast",
    "get_backtest",
    "get_chart_drawings",
    "get_metric_series",
    "get_ohlcv",
    "get_pending_ui_events",
    "get_track_record",
    "highlight_pattern",
    "list_alerts",
    "list_annotations",
    "list_watches",
    "market_snapshot",
    "multi_timeframe_analysis",
    "news_for",
    "portfolio_summary",
    "prediction_market_odds",
    "price_structure",
    "quote_for",
    "recommend",
    "run_backtest",
    "scan_patterns",
    "scan_pool_discrepancies",
    "scan_wallet",
    "scan_watchlist",
    "screener_query",
    "search_prediction_markets",
    "search_symbols",
    "sentiment",
    "show_chart",
    "technical_read",
    "update_chart",
    "volume_read",
    "walk_forward_backtest",
    "write_annotation",
}


def test_full_toolset_registration_is_exhaustive(tmp_path: Path) -> None:
    # The fully-wired construction (every conditional dependency present) lives in
    # `apiref.wiring` as one source of truth, shared with the reference generator
    # (Plan 0070). `build_wired_mcp_server` returns the `FastMCP` instance; its
    # public async `list_tools()` is the same surface the agent sees.
    import anyio

    from market_analyser.apiref.wiring import build_wired_mcp_server

    server = build_wired_mcp_server(tmp_path / "runs")
    names = {tool.name for tool in anyio.run(server.list_tools)}
    assert names == EXPECTED_FULL_TOOLSET, (
        f"missing: {sorted(EXPECTED_FULL_TOOLSET - names)}; "
        f"unexpected: {sorted(names - EXPECTED_FULL_TOOLSET)}"
    )
