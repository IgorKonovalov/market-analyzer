"""Plan 0020 phase 3: the `compare_strategies` MCP tool end-to-end.

Uses a real uvicorn loopback server + MCP ClientSession so the Streamable
HTTP transport is exercised the way Claude Desktop would — mirrors
`test_run_backtest_tool.py`.

Done-when covered:
- leaderboard: one row per discovered strategy, ordered by the rank metric
  descending (None last, strategy_id asc tie-break); deterministic across
  two calls;
- rank_by accepts sharpe|calmar|total_return|sortino and orders by each;
- an out-of-set rank_by is rejected at the MCP boundary;
- rows carry the extended ADR-0024 metric set.
"""

from __future__ import annotations

import asyncio
import math
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

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.contracts.strategy import discover
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
_RANK_BYS = ("sharpe", "calmar", "total_return", "sortino")


def _bars(symbol: str = "AAPL", n: int = 200) -> list[Bar]:
    """Sine-plus-trend bars (the golden-fixture shape) so several reference
    strategies cross and produce distinct metrics."""

    start = datetime(2025, 1, 1, tzinfo=UTC)
    closes = [100.0 + math.sin(i / 10.0) * 12.0 + (i / n) * 18.0 for i in range(n)]
    bars: list[Bar] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        bar_open = prev_close
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=start + timedelta(days=i),
                open=bar_open,
                high=max(bar_open, close),
                low=min(bar_open, close),
                close=close,
                volume=1_000_000.0,
                source="test",
            )
        )
        prev_close = close
    return bars


class _BarsProvider:
    """Returns a deterministic bar list regardless of (symbol, range)."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
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

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError


@pytest.fixture
def mcp_secret(tmp_path: Path) -> str:
    return load_or_generate_mcp_secret(tmp_path / "mcp-secret.json")


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
        provider=_BarsProvider(bars=_bars()),
        annotations_repository=annotations_repo,
        event_bus=EventBus(),
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


def _params(rank_by: str = "sharpe") -> dict[str, object]:
    return {
        "symbol": "AAPL",
        "timeframe": "1d",
        "range_start": "2025-01-01T00:00:00+00:00",
        "range_end": "2025-12-31T00:00:00+00:00",
        "rank_by": rank_by,
    }


async def _call(url: str, secret: str, params: dict[str, object]) -> dict[str, object]:
    async with _mcp_session(url, secret) as session:
        result = await session.call_tool("compare_strategies", params)
        assert not result.isError, f"compare_strategies errored: {result.content}"
        assert result.structuredContent is not None
        return dict(result.structuredContent)


def _expected_order(rows: list[dict[str, object]], rank_by: str) -> list[str]:
    """Re-derive the ranked strategy_id order from the row data independently."""

    def key(row: dict[str, object]) -> tuple[bool, float, str]:
        metrics = row["metrics"]
        assert isinstance(metrics, dict)
        value = metrics[rank_by]
        assert value is None or isinstance(value, (int, float))
        return (
            value is None,
            -(float(value) if value is not None else 0.0),
            str(row["strategy_id"]),
        )

    return [str(r["strategy_id"]) for r in sorted(rows, key=key)]


def test_leaderboard_has_one_row_per_strategy_ranked_by_sharpe(
    live_server: str, mcp_secret: str
) -> None:
    payload = asyncio.run(_call(live_server, mcp_secret, _params("sharpe")))
    rows = payload["rows"]
    assert isinstance(rows, list)
    returned_ids = {str(r["strategy_id"]) for r in rows}
    assert returned_ids == set(discover())
    assert payload["rank_by"] == "sharpe"
    # Returned order matches an independent descending sort (None last, id asc).
    order = [str(r["strategy_id"]) for r in rows]
    assert order == _expected_order(rows, "sharpe")


def test_order_is_deterministic_across_two_calls(live_server: str, mcp_secret: str) -> None:
    async def _run() -> tuple[list[str], list[str]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            r1 = await session.call_tool("compare_strategies", _params("sharpe"))
            r2 = await session.call_tool("compare_strategies", _params("sharpe"))
            assert r1.structuredContent is not None and r2.structuredContent is not None
            o1 = [str(r["strategy_id"]) for r in list(r1.structuredContent["rows"])]
            o2 = [str(r["strategy_id"]) for r in list(r2.structuredContent["rows"])]
            return o1, o2

    o1, o2 = asyncio.run(_run())
    assert o1 == o2


@pytest.mark.parametrize("rank_by", _RANK_BYS)
def test_each_rank_by_orders_by_that_metric(
    live_server: str, mcp_secret: str, rank_by: str
) -> None:
    payload = asyncio.run(_call(live_server, mcp_secret, _params(rank_by)))
    rows = payload["rows"]
    assert isinstance(rows, list)
    order = [str(r["strategy_id"]) for r in rows]
    assert order == _expected_order(rows, rank_by)


def test_rows_carry_extended_metrics(live_server: str, mcp_secret: str) -> None:
    payload = asyncio.run(_call(live_server, mcp_secret, _params("sharpe")))
    rows = payload["rows"]
    assert isinstance(rows, list) and rows
    metrics = rows[0]["metrics"]
    assert isinstance(metrics, dict)
    for field in ("calmar", "sortino", "profit_factor", "expectancy", "best_trade_return"):
        assert field in metrics


def test_out_of_set_rank_by_rejected_at_boundary(live_server: str, mcp_secret: str) -> None:
    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            try:
                result = await session.call_tool("compare_strategies", _params("win_rate"))
            except Exception:
                return True
            return bool(result.isError)

    assert asyncio.run(_run()), "rank_by='win_rate' must be rejected at the MCP boundary"
