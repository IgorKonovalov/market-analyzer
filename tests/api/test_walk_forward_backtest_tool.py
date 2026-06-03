"""Plan 0020 phase 3: the `walk_forward_backtest` MCP tool end-to-end.

Real uvicorn loopback + MCP ClientSession, mirroring
`test_run_backtest_tool.py`.

Done-when covered:
- returns the per-fold + aggregate report (n_splits folds, aggregate keys,
  full-run baseline);
- no lookahead surfaced through the tool path (fold k starts strictly
  after fold k-1 ends);
- unknown strategy_id surfaces a typed MCP error (not a 500);
- an invalid n_splits is rejected;
- determinism: two identical calls produce identical fold reports.
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


def _bars(symbol: str = "AAPL", n: int = 200) -> list[Bar]:
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


def _params(strategy_id: str = "rsi", n_splits: int = 4) -> dict[str, object]:
    return {
        "strategy_id": strategy_id,
        "symbol": "AAPL",
        "timeframe": "1d",
        "range_start": "2025-01-01T00:00:00+00:00",
        "range_end": "2025-12-31T00:00:00+00:00",
        "n_splits": n_splits,
    }


async def _call_ok(url: str, secret: str, params: dict[str, object]) -> dict[str, object]:
    async with _mcp_session(url, secret) as session:
        result = await session.call_tool("walk_forward_backtest", params)
        assert not result.isError, f"walk_forward_backtest errored: {result.content}"
        assert result.structuredContent is not None
        return dict(result.structuredContent)


def test_returns_per_fold_and_aggregate_report(live_server: str, mcp_secret: str) -> None:
    payload = asyncio.run(_call_ok(live_server, mcp_secret, _params(n_splits=4)))
    assert payload["strategy_id"] == "rsi"
    assert payload["n_splits"] == 4
    folds = payload["folds"]
    assert isinstance(folds, list)
    assert len(folds) == 4
    assert [f["fold_index"] for f in folds] == [0, 1, 2, 3]
    aggregate = payload["aggregate"]
    assert isinstance(aggregate, dict)
    assert set(aggregate.keys()) == {
        "total_return_mean",
        "total_return_std",
        "sharpe_mean",
        "sharpe_std",
    }
    assert isinstance(payload["full_run_baseline"], dict)


def test_no_lookahead_surfaced_through_tool(live_server: str, mcp_secret: str) -> None:
    payload = asyncio.run(_call_ok(live_server, mcp_secret, _params(n_splits=4)))
    folds = payload["folds"]
    assert isinstance(folds, list)
    starts = [datetime.fromisoformat(str(f["range_start"])) for f in folds]
    ends = [datetime.fromisoformat(str(f["range_end"])) for f in folds]
    # Fold k's first bar strictly follows fold k-1's last bar.
    for k in range(1, len(folds)):
        assert starts[k] > ends[k - 1]


def test_determinism_two_identical_calls(live_server: str, mcp_secret: str) -> None:
    async def _run() -> tuple[dict[str, object], dict[str, object]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            r1 = await session.call_tool("walk_forward_backtest", _params(n_splits=4))
            r2 = await session.call_tool("walk_forward_backtest", _params(n_splits=4))
            assert r1.structuredContent is not None and r2.structuredContent is not None
            return dict(r1.structuredContent), dict(r2.structuredContent)

    p1, p2 = asyncio.run(_run())
    assert p1 == p2


def test_unknown_strategy_id_is_typed_mcp_error_not_500(live_server: str, mcp_secret: str) -> None:
    async def _run() -> tuple[bool, str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            # No transport-level exception (a 500 would raise here): the tool
            # surfaces a structured isError result instead.
            result = await session.call_tool("walk_forward_backtest", _params(strategy_id="nope"))
            return result.isError, "\n".join(str(c) for c in (result.content or []))

    errored, message = asyncio.run(_run())
    assert errored, "unknown strategy_id must surface an MCP error"
    assert "nope" in message


def test_invalid_n_splits_rejected(live_server: str, mcp_secret: str) -> None:
    async def _run(n_splits: int) -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            try:
                result = await session.call_tool(
                    "walk_forward_backtest", _params(n_splits=n_splits)
                )
            except Exception:
                return True
            return bool(result.isError)

    # n_splits=0 is below the floor; n_splits beyond the bar count is too many.
    assert asyncio.run(_run(0)), "n_splits=0 must be rejected"
    assert asyncio.run(_run(10_000)), "n_splits > bar count must be rejected"
