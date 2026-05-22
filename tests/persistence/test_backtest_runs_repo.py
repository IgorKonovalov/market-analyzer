"""Plan 0008 phase 3: BacktestRunsRepository — list/get filters + ordering."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.backtest.result import BacktestRunSummary
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.backtest_runs import (
    MAX_LIST_LIMIT,
    BacktestRunsRepository,
)


def _summary(
    *,
    run_id: str,
    strategy_id: str = "rsi",
    symbol: str = "AAPL",
    timeframe: str = "1d",
    finished_offset_seconds: int = 0,
    engine_version: str = "0.1.0",
) -> BacktestRunSummary:
    finished_at = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC) + timedelta(
        seconds=finished_offset_seconds,
    )
    return BacktestRunSummary(
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        symbol=symbol,
        timeframe=timeframe,
        range_start=datetime(2026, 4, 1, tzinfo=UTC),
        range_end=datetime(2026, 5, 1, tzinfo=UTC),
        total_return=0.12,
        sharpe=1.5,
        max_drawdown=-0.05,
        win_rate=0.6,
        trade_count=10,
        finished_at=finished_at,
        artifact_path=run_id,
        engine_version=engine_version,
    )


@pytest.fixture
def repo() -> Iterator[BacktestRunsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BacktestRunsRepository(make_session_factory(engine))
    engine.dispose()


def test_insert_then_get_round_trips_all_fields(repo: BacktestRunsRepository) -> None:
    original = _summary(run_id="abc123")
    repo.insert(original)
    got = repo.get("abc123")
    assert got is not None
    assert got == original


def test_get_unknown_run_id_returns_none(repo: BacktestRunsRepository) -> None:
    assert repo.get("does-not-exist") is None


def test_get_empty_run_id_raises(repo: BacktestRunsRepository) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        repo.get("")


def test_list_filters_by_symbol(repo: BacktestRunsRepository) -> None:
    repo.insert(_summary(run_id="r1", symbol="AAPL"))
    repo.insert(_summary(run_id="r2", symbol="AAPL"))
    repo.insert(_summary(run_id="r3", symbol="MSFT"))

    aapl = repo.list(symbol="AAPL")
    assert {s.run_id for s in aapl} == {"r1", "r2"}
    assert all(s.symbol == "AAPL" for s in aapl)


def test_list_filters_by_strategy_id_and_symbol_compose_with_and(
    repo: BacktestRunsRepository,
) -> None:
    repo.insert(_summary(run_id="r1", symbol="AAPL", strategy_id="rsi"))
    repo.insert(_summary(run_id="r2", symbol="AAPL", strategy_id="macd"))
    repo.insert(_summary(run_id="r3", symbol="MSFT", strategy_id="rsi"))

    aapl_rsi = repo.list(symbol="AAPL", strategy_id="rsi")
    assert [s.run_id for s in aapl_rsi] == ["r1"]


def test_list_ordered_by_finished_at_descending(repo: BacktestRunsRepository) -> None:
    repo.insert(_summary(run_id="oldest", finished_offset_seconds=0))
    repo.insert(_summary(run_id="middle", finished_offset_seconds=60))
    repo.insert(_summary(run_id="newest", finished_offset_seconds=120))

    out = repo.list()
    assert [s.run_id for s in out] == ["newest", "middle", "oldest"]


def test_list_limit_caps_results(repo: BacktestRunsRepository) -> None:
    for i in range(5):
        repo.insert(_summary(run_id=f"r{i}", finished_offset_seconds=i))
    out = repo.list(limit=2)
    assert len(out) == 2


def test_list_limit_below_one_raises(repo: BacktestRunsRepository) -> None:
    with pytest.raises(ValueError, match="limit must be in"):
        repo.list(limit=0)


def test_list_limit_above_max_raises(repo: BacktestRunsRepository) -> None:
    with pytest.raises(ValueError, match="limit must be in"):
        repo.list(limit=MAX_LIST_LIMIT + 1)


def test_list_filters_symbol_case_insensitive(repo: BacktestRunsRepository) -> None:
    """`AAPL` rows should be findable via `aapl` filter — mirrors BarRepository."""
    repo.insert(_summary(run_id="r1", symbol="AAPL"))
    out = repo.list(symbol="aapl")
    assert [s.run_id for s in out] == ["r1"]


def test_duplicate_run_id_insert_raises(repo: BacktestRunsRepository) -> None:
    """PK collision surfaces on insert — caller is responsible for the
    atomic disk-then-DB ordering in persist()."""
    from sqlalchemy.exc import IntegrityError

    repo.insert(_summary(run_id="dup"))
    with pytest.raises(IntegrityError):
        repo.insert(_summary(run_id="dup"))


def test_list_empty_repository_returns_empty_list(repo: BacktestRunsRepository) -> None:
    assert repo.list() == []
