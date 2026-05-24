"""Plan 0008 phase 3: GET /backtests and GET /backtests/{run_id}.

Done-when:
- 200 + round-trip BacktestResult with renderer bearer
- 401 without bearer / with the MCP bearer (cross-tenant isolation)
- 404 for unknown run_id
- summary list sorted by finished_at desc, capped by ?limit=
- no bearer string in access logs
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.backtest.persistence import persist
from market_analyser.backtest.result import (
    BacktestMetrics,
    BacktestResult,
    EquityPoint,
)
from market_analyser.backtest.types import Trade
from market_analyser.data.types import (
    Bar,
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
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)

RENDERER_SECRET = "renderer-test-secret"
MCP_SECRET = "mcp-test-secret"


class _UnusedProvider:
    """Provider stub: /backtests must not call the provider — fail loud if it does."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise AssertionError("backtests route must not call the provider")

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

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


def _result(
    *,
    run_id: str = "11111111111111111111111111111111",
    symbol: str = "AAPL",
    strategy_id: str = "rsi",
    finished_offset_seconds: int = 0,
) -> BacktestResult:
    finished_at = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC) + timedelta(
        seconds=finished_offset_seconds,
    )
    return BacktestResult(
        run_id=run_id,
        engine_version="0.1.0",
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        symbol=symbol,
        timeframe="1d",
        range_start=datetime(2026, 4, 1, tzinfo=UTC),
        range_end=datetime(2026, 5, 1, tzinfo=UTC),
        bars_hash="deadbeef" * 8,
        params={"period": 14, "oversold": 30, "overbought": 70},
        costs={"commission_bps": 0.0, "slippage_bps": 0.0},
        initial_capital=10_000.0,
        sizing="fixed_fraction",
        started_at=finished_at - timedelta(seconds=1),
        finished_at=finished_at,
        trades=[
            Trade(
                entry_bar_index=1,
                exit_bar_index=2,
                entry_price=100.0,
                exit_price=110.0,
                kind="long",
            ),
        ],
        equity_curve=[
            EquityPoint(
                ts=datetime(2026, 4, 1, tzinfo=UTC) + timedelta(days=i),
                equity=10_000.0 + 100.0 * i,
            )
            for i in range(3)
        ],
        metrics=BacktestMetrics(
            total_return=0.10,
            sharpe=1.5,
            max_drawdown=-0.02,
            max_drawdown_duration_bars=1,
            win_rate=1.0,
            trade_count=1,
            buy_and_hold_return=0.08,
        ),
    )


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


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


@pytest.fixture
def client(
    annotations_repo: AnnotationsRepository,
    repo: BacktestRunsRepository,
    runs_dir: Path,
) -> TestClient:
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        provider=_UnusedProvider(),
        annotations_repository=annotations_repo,
        backtest_runs_repository=repo,
        runs_dir=runs_dir,
    )
    return TestClient(app)


def _renderer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {MCP_SECRET}"}


def test_get_backtest_by_id_returns_round_trip_result(
    client: TestClient, repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    original = _result(run_id="abc11111111111111111111111111111")
    persist(original, runs_dir, repo)
    response = client.get(f"/backtests/{original.run_id}", headers=_renderer_auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == original.run_id
    assert body["strategy_id"] == "rsi"
    assert body["symbol"] == "AAPL"
    assert isinstance(body["equity_curve"], list)
    assert len(body["equity_curve"]) == len(original.equity_curve)
    assert body["metrics"]["total_return"] == pytest.approx(0.10)
    assert body["metrics"]["sharpe"] == pytest.approx(1.5)


def test_get_backtest_unknown_id_returns_404(client: TestClient) -> None:
    response = client.get("/backtests/no-such-run", headers=_renderer_auth())
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail
    assert "no-such-run" in detail


def test_get_backtest_without_bearer_returns_401(client: TestClient) -> None:
    response = client.get("/backtests/abc")
    assert response.status_code == 401


def test_get_backtest_with_mcp_bearer_returns_401(client: TestClient) -> None:
    """Cross-tenant isolation: MCP bearer must not authenticate the renderer route."""
    response = client.get("/backtests/abc", headers=_mcp_auth())
    assert response.status_code == 401


def test_list_backtests_sorted_by_finished_at_descending(
    client: TestClient, repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    for i, run_id in enumerate(
        (
            "oldest11111111111111111111111111",
            "middle11111111111111111111111111",
            "newest11111111111111111111111111",
        )
    ):
        persist(_result(run_id=run_id, finished_offset_seconds=i * 60), runs_dir, repo)
    response = client.get("/backtests", headers=_renderer_auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["run_id"] for row in body] == [
        "newest11111111111111111111111111",
        "middle11111111111111111111111111",
        "oldest11111111111111111111111111",
    ]


def test_list_backtests_limit_caps_results(
    client: TestClient, repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    for i in range(3):
        persist(
            _result(run_id=f"{i:032x}", finished_offset_seconds=i),
            runs_dir,
            repo,
        )
    response = client.get("/backtests", params={"limit": 2}, headers=_renderer_auth())
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_backtests_limit_above_max_returns_422(client: TestClient) -> None:
    response = client.get("/backtests", params={"limit": 99999}, headers=_renderer_auth())
    assert response.status_code == 422


def test_list_backtests_without_bearer_returns_401(client: TestClient) -> None:
    response = client.get("/backtests")
    assert response.status_code == 401


def test_list_backtests_with_mcp_bearer_returns_401(client: TestClient) -> None:
    response = client.get("/backtests", headers=_mcp_auth())
    assert response.status_code == 401


def test_get_backtest_with_orphaned_index_row_returns_404(
    client: TestClient, repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    """Index row exists but the artifact is gone on disk — 404 with a distinct
    detail so the cause is visible in logs."""
    from market_analyser.backtest.result import BacktestRunSummary

    repo.insert(
        BacktestRunSummary(
            run_id="orphan11111111111111111111111111",
            strategy_id="rsi",
            strategy_version="1.0.0",
            symbol="AAPL",
            timeframe="1d",
            range_start=datetime(2026, 4, 1, tzinfo=UTC),
            range_end=datetime(2026, 5, 1, tzinfo=UTC),
            total_return=0.1,
            sharpe=1.5,
            max_drawdown=-0.02,
            win_rate=1.0,
            trade_count=1,
            finished_at=datetime(2026, 5, 22, tzinfo=UTC),
            artifact_path="orphan11111111111111111111111111",
            engine_version="0.1.0",
        ),
    )
    response = client.get(
        "/backtests/orphan11111111111111111111111111",
        headers=_renderer_auth(),
    )
    assert response.status_code == 404
    assert "missing on disk" in response.json()["detail"]


def test_routes_do_not_leak_bearer_in_logs(
    client: TestClient,
    repo: BacktestRunsRepository,
    runs_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Phase-3 security checklist: neither the renderer nor the MCP bearer
    appears in captured log records when the routes are exercised."""
    persist(_result(run_id="logleak11111111111111111111111x"), runs_dir, repo)
    with caplog.at_level(logging.DEBUG):
        client.get(
            "/backtests/logleak11111111111111111111111x",
            headers=_renderer_auth(),
        )
        client.get("/backtests", headers=_renderer_auth())
        client.get("/backtests", headers=_mcp_auth())  # 401 path

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert RENDERER_SECRET not in joined
    assert MCP_SECRET not in joined


def test_list_backtests_filter_symbol_excludes_others(
    client: TestClient, repo: BacktestRunsRepository, runs_dir: Path
) -> None:
    persist(_result(run_id="aapl1111111111111111111111111111", symbol="AAPL"), runs_dir, repo)
    persist(_result(run_id="msft1111111111111111111111111111", symbol="MSFT"), runs_dir, repo)
    response = client.get("/backtests", params={"symbol": "AAPL"}, headers=_renderer_auth())
    assert response.status_code == 200
    body = response.json()
    assert {row["run_id"] for row in body} == {"aapl1111111111111111111111111111"}
