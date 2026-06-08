"""Plan 0050 phase 3: the `get_backtest` MCP tool (finding #1, ADR-0046).

The tool reads a persisted `BacktestResult` via `BacktestRunsRepository.get` +
`read_result` (the same artifact path the renderer-bearer REST route uses) and
returns metrics + spec + the full trade list inline; the equity curve is opt-in
and paged. An unknown run_id is a typed not-found error, not a 500.

The body (`_get_backtest_response`) is unit-tested directly against a persisted
result for the trades/equity/not-found behaviour; one live-MCP test proves the
tool is registered and reachable end-to-end over the real transport.
"""

from __future__ import annotations

import asyncio
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
from market_analyser.api.mcp_tools.get_backtest import (
    MAX_EQUITY_POINTS,
    BacktestNotFoundError,
    _get_backtest_response,
)
from market_analyser.backtest.persistence import persist
from market_analyser.backtest.result import (
    BacktestMetrics,
    BacktestResult,
    EquityPoint,
)
from market_analyser.backtest.types import Trade
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
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)

RENDERER_SECRET = "renderer-test-secret"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_result(*, run_id: str, n_trades: int, n_equity: int) -> BacktestResult:
    """Build a persistable `BacktestResult` with `n_trades` closed trades and an
    `n_equity`-point equity curve."""
    trades = [
        Trade(
            entry_bar_index=i * 3,
            exit_bar_index=i * 3 + 2,
            entry_price=100.0 + i,
            exit_price=101.0 + i,
            kind="long",
        )
        for i in range(n_trades)
    ]
    equity_curve = [
        EquityPoint(ts=_T0 + timedelta(days=i), equity=10_000.0 + i) for i in range(n_equity)
    ]
    return BacktestResult(
        run_id=run_id,
        engine_version="test-engine",
        strategy_id="rsi",
        strategy_version="1",
        symbol="AAPL",
        timeframe="1d",
        range_start=_T0,
        range_end=_T0 + timedelta(days=max(1, n_equity)),
        bars_hash="deadbeef",
        params={"period": 14, "oversold": 30, "overbought": 70},
        costs={"commission_bps": 5.0, "slippage_bps": 5.0},
        initial_capital=10_000.0,
        sizing="fixed_fraction",
        started_at=_T0,
        finished_at=_T0 + timedelta(seconds=1),
        trades=trades,
        equity_curve=equity_curve,
        metrics=BacktestMetrics(
            total_return=0.1,
            sharpe=1.2,
            max_drawdown=-0.05,
            max_drawdown_duration_bars=3,
            win_rate=0.6,
            trade_count=n_trades,
            buy_and_hold_return=0.08,
        ),
    )


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def repo() -> Iterator[BacktestRunsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BacktestRunsRepository(make_session_factory(engine))
    engine.dispose()


def _persisted(repo: BacktestRunsRepository, runs_dir: Path, result: BacktestResult) -> str:
    persist(result, runs_dir, repo)
    return result.run_id


# --------------------------------------------------------------------------- #
# Default: full trades + metrics, no equity                                    #
# --------------------------------------------------------------------------- #


def test_returns_full_trades_and_metrics_no_equity_by_default(
    repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    run_id = _persisted(repo, runs_dir, _make_result(run_id="a" * 32, n_trades=4, n_equity=10))

    resp = _get_backtest_response(
        repository=repo,
        runs_dir=runs_dir,
        run_id=run_id,
        include_equity=False,
        equity_offset=0,
        max_equity_points=None,
    )

    assert resp.run_id == run_id
    assert len(resp.trades) == 4
    assert resp.trades[0].entry_bar_index == 0
    assert resp.trades[0].exit_price == 101.0
    assert resp.metrics.trade_count == 4
    assert resp.metrics.sharpe == 1.2
    assert resp.symbol == "AAPL"
    assert resp.params == {"period": 14, "oversold": 30, "overbought": 70}
    assert resp.equity is None  # omitted by default


# --------------------------------------------------------------------------- #
# Equity opt-in + paging                                                       #
# --------------------------------------------------------------------------- #


def test_include_equity_over_cap_is_paged_and_flagged_too_large(
    repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    total = MAX_EQUITY_POINTS + 250
    run_id = _persisted(repo, runs_dir, _make_result(run_id="b" * 32, n_trades=1, n_equity=total))

    resp = _get_backtest_response(
        repository=repo,
        runs_dir=runs_dir,
        run_id=run_id,
        include_equity=True,
        equity_offset=0,
        max_equity_points=None,
    )

    assert resp.equity is not None
    assert resp.equity.returned == MAX_EQUITY_POINTS
    assert len(resp.equity.points) == MAX_EQUITY_POINTS
    assert resp.equity.total_available == total
    assert resp.equity.partial_reason == "too_large"
    assert resp.equity.message is not None

    # Second page is the contiguous remainder.
    page2 = _get_backtest_response(
        repository=repo,
        runs_dir=runs_dir,
        run_id=run_id,
        include_equity=True,
        equity_offset=MAX_EQUITY_POINTS,
        max_equity_points=None,
    )
    assert page2.equity is not None
    assert page2.equity.returned == 250
    assert page2.equity.partial_reason is None
    assert page2.equity.points[0].ts == resp.equity.points[-1].ts + timedelta(days=1)


def test_include_equity_under_cap_returns_all_unflagged(
    repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    run_id = _persisted(repo, runs_dir, _make_result(run_id="c" * 32, n_trades=1, n_equity=12))

    resp = _get_backtest_response(
        repository=repo,
        runs_dir=runs_dir,
        run_id=run_id,
        include_equity=True,
        equity_offset=0,
        max_equity_points=None,
    )

    assert resp.equity is not None
    assert resp.equity.returned == 12
    assert resp.equity.total_available == 12
    assert resp.equity.partial_reason is None


# --------------------------------------------------------------------------- #
# Not-found: typed error, not a 500                                            #
# --------------------------------------------------------------------------- #


def test_unknown_run_id_raises_not_found(
    repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    with pytest.raises(BacktestNotFoundError) as excinfo:
        _get_backtest_response(
            repository=repo,
            runs_dir=runs_dir,
            run_id="does-not-exist",
            include_equity=False,
            equity_offset=0,
            max_equity_points=None,
        )
    assert "does-not-exist" in str(excinfo.value)


def test_missing_artifact_on_disk_raises_not_found(
    repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    run_id = _persisted(repo, runs_dir, _make_result(run_id="d" * 32, n_trades=1, n_equity=5))
    # Delete the artifact out from under the index row.
    import shutil

    shutil.rmtree(runs_dir / run_id)

    with pytest.raises(BacktestNotFoundError):
        _get_backtest_response(
            repository=repo,
            runs_dir=runs_dir,
            run_id=run_id,
            include_equity=False,
            equity_offset=0,
            max_equity_points=None,
        )


@pytest.mark.parametrize(("equity_offset", "max_equity_points"), [(-1, None), (0, 0), (0, -3)])
def test_invalid_paging_params_raise(
    repo: BacktestRunsRepository, runs_dir: Path, equity_offset: int, max_equity_points: int | None
) -> None:
    run_id = _persisted(repo, runs_dir, _make_result(run_id="e" * 32, n_trades=1, n_equity=5))
    with pytest.raises(ValueError):
        _get_backtest_response(
            repository=repo,
            runs_dir=runs_dir,
            run_id=run_id,
            include_equity=True,
            equity_offset=equity_offset,
            max_equity_points=max_equity_points,
        )


# --------------------------------------------------------------------------- #
# Live MCP server: registration + end-to-end over the real transport           #
# --------------------------------------------------------------------------- #


class _BarsProvider:
    """Deterministic bar list (enough movement for RSI signals), Protocol-complete."""

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


def _rsi_bars(n: int = 80) -> list[Bar]:
    bars: list[Bar] = []
    price = 100.0
    for i in range(n):
        price += 1.0 if i % 20 < 10 else -0.8
        bars.append(
            Bar(
                symbol="AAPL",
                timeframe="1d",
                event_ts=_T0 + timedelta(days=i),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1_000_000.0,
                source="test",
            )
        )
    return bars


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
def app(
    mcp_secret: str,
    annotations_repo: AnnotationsRepository,
    repo: BacktestRunsRepository,
    runs_dir: Path,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_BarsProvider(bars=_rsi_bars()),
        annotations_repository=annotations_repo,
        backtest_runs_repository=repo,
        runs_dir=runs_dir,
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


def test_get_backtest_registered_and_returns_trades_over_mcp(
    live_server: str, mcp_secret: str
) -> None:
    """get_backtest is registered (alongside run_backtest) and, given a freshly-run
    run_id, returns the trades inline with no equity over the real transport."""

    async def _run() -> tuple[set[str], dict[str, object]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}

            run = await session.call_tool(
                "run_backtest",
                {
                    "strategy_id": "rsi",
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "range_start": "2026-01-01T00:00:00+00:00",
                    "range_end": "2026-04-30T00:00:00+00:00",
                    "params": {"period": 14, "oversold": 30, "overbought": 70},
                },
            )
            assert not run.isError, f"run_backtest errored: {run.content}"
            assert run.structuredContent is not None
            run_id = run.structuredContent["run_id"]

            got = await session.call_tool("get_backtest", {"run_id": run_id})
            assert not got.isError, f"get_backtest errored: {got.content}"
            assert got.structuredContent is not None
            return names, dict(got.structuredContent)

    names, payload = asyncio.run(_run())
    assert "get_backtest" in names
    assert payload["symbol"] == "AAPL"
    assert isinstance(payload["trades"], list)
    assert isinstance(payload["metrics"], dict)
    assert payload["equity"] is None  # opt-in only


def test_get_backtest_unknown_run_id_is_error_not_500(
    live_server: str, mcp_secret: str
) -> None:
    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("get_backtest", {"run_id": "nope"})
            return bool(result.isError)

    assert asyncio.run(_run()) is True
