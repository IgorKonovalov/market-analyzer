"""Plan 0008 phase 3: persist() + read_result() atomicity, spec contents, round-trip."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_analyser.backtest.persistence import (
    EQUITY_CURVE_FILENAME,
    RESULT_FILENAME,
    SPEC_FILENAME,
    SPEC_KEYS,
    persist,
    read_result,
)
from market_analyser.backtest.result import (
    BacktestMetrics,
    BacktestResult,
    BacktestRunSummary,
    EquityPoint,
)
from market_analyser.backtest.types import Trade
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)


def _result(
    *,
    run_id: str = "abcdef0123456789abcdef0123456789",
    n_equity: int = 3,
) -> BacktestResult:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    return BacktestResult(
        run_id=run_id,
        engine_version="0.1.0",
        strategy_id="rsi",
        strategy_version="1.0.0",
        symbol="AAPL",
        timeframe="1d",
        range_start=start,
        range_end=datetime(2026, 4, 30, tzinfo=UTC),
        bars_hash="deadbeef" * 8,
        params={"period": 14, "oversold": 30, "overbought": 70},
        costs={"commission_bps": 0.0, "slippage_bps": 0.0},
        initial_capital=10_000.0,
        sizing="fixed_fraction",
        started_at=datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 30, 12, 0, 1, tzinfo=UTC),
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
                ts=datetime(2026, 4, 1 + i, tzinfo=UTC),
                equity=10_000.0 + 100.0 * i,
            )
            for i in range(n_equity)
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
def repo() -> Iterator[BacktestRunsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BacktestRunsRepository(make_session_factory(engine))
    engine.dispose()


def test_persist_writes_three_files_and_returns_run_id_path(
    tmp_path: Path, repo: BacktestRunsRepository
) -> None:
    result = _result()
    final_dir = persist(result, tmp_path, repo)
    assert final_dir == tmp_path / result.run_id
    assert final_dir.is_dir()
    files = {p.name for p in final_dir.iterdir()}
    assert files == {SPEC_FILENAME, RESULT_FILENAME, EQUITY_CURVE_FILENAME}


def test_spec_json_contains_only_spec_keys(tmp_path: Path, repo: BacktestRunsRepository) -> None:
    """Plan §165: spec.json holds exactly the re-runnable spec, nothing more."""
    import json

    result = _result()
    final_dir = persist(result, tmp_path, repo)
    spec = json.loads((final_dir / SPEC_FILENAME).read_text(encoding="utf-8"))
    assert set(spec.keys()) == SPEC_KEYS
    for forbidden in (
        "run_id",
        "started_at",
        "finished_at",
        "engine_version",
        "trades",
        "equity_curve",
        "metrics",
    ):
        assert forbidden not in spec


def test_equity_curve_csv_has_two_columns_and_one_row_per_bar(
    tmp_path: Path, repo: BacktestRunsRepository
) -> None:
    import csv

    result = _result(n_equity=5)
    final_dir = persist(result, tmp_path, repo)
    with (final_dir / EQUITY_CURVE_FILENAME).open("r", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["ts", "equity"]
    assert len(rows) == len(result.equity_curve) + 1
    # Spot-check round-trip: ts parses to UTC datetime, equity to float.
    first_data = rows[1]
    parsed_ts = datetime.fromisoformat(first_data[0])
    assert parsed_ts == result.equity_curve[0].ts
    assert float(first_data[1]) == result.equity_curve[0].equity


def test_read_result_round_trips_with_persist(tmp_path: Path, repo: BacktestRunsRepository) -> None:
    """Disk round-trip: model_dump equality before/after persist+read."""
    original = _result()
    final_dir = persist(original, tmp_path, repo)
    rehydrated = read_result(final_dir)
    assert rehydrated.model_dump() == original.model_dump()


def test_persist_inserts_summary_row(tmp_path: Path, repo: BacktestRunsRepository) -> None:
    result = _result()
    persist(result, tmp_path, repo)
    summary = repo.get(result.run_id)
    assert summary is not None
    assert summary.run_id == result.run_id
    assert summary.strategy_id == "rsi"
    assert summary.total_return == result.metrics.total_return
    assert summary.sharpe == result.metrics.sharpe
    assert summary.max_drawdown == result.metrics.max_drawdown
    assert summary.win_rate == result.metrics.win_rate
    assert summary.trade_count == result.metrics.trade_count
    assert summary.artifact_path == result.run_id
    assert summary.engine_version == result.engine_version


def test_persist_atomic_when_db_insert_fails(tmp_path: Path, repo: BacktestRunsRepository) -> None:
    """Plan §167: a duplicate run_id leaves no orphan files behind."""
    result = _result()
    # Pre-seed the SQLite row with the same run_id so the second insert
    # raises an IntegrityError on the persist() commit.
    repo.insert(
        BacktestRunSummary(
            run_id=result.run_id,
            strategy_id="precommit",
            strategy_version="0.0.0",
            symbol="AAPL",
            timeframe="1d",
            range_start=result.range_start,
            range_end=result.range_end,
            total_return=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            trade_count=0,
            finished_at=result.finished_at,
            artifact_path=result.run_id,
            engine_version="0.1.0",
        ),
    )
    with pytest.raises(Exception):
        persist(result, tmp_path, repo)

    assert not (tmp_path / result.run_id).exists(), (
        "persist() must roll back the artifact directory on SQLite failure"
    )
    # No orphaned temp dir either.
    temp_dirs = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert temp_dirs == []


def test_persist_refuses_existing_artifact_dir(
    tmp_path: Path, repo: BacktestRunsRepository
) -> None:
    """A pre-existing run_id directory means somebody else owns the slot.
    persist() must refuse rather than scribble over it."""
    result = _result()
    (tmp_path / result.run_id).mkdir()
    with pytest.raises(FileExistsError):
        persist(result, tmp_path, repo)


def test_read_result_raises_on_missing_files(tmp_path: Path) -> None:
    """A partial artifact is a bug. read_result refuses to half-load."""
    empty_dir = tmp_path / "empty-run"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        read_result(empty_dir)
