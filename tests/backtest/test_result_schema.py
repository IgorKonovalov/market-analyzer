"""`BacktestResult` schema acceptance tests.

Per Plan 0008 phase 1 done-when:

- Constructing with all fields succeeds.
- Constructing with an unknown field raises ValidationError (extra="forbid"
  defends the schema's append-only discipline).
- Round-trip via `model_dump(mode="json")` produces an equal object — the
  Pydantic mode="json"/mode="python" symmetry the determinism contract
  depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from market_analyser.backtest import (
    BacktestMetrics,
    BacktestResult,
    EquityPoint,
    Trade,
)


def _hand_built_result() -> BacktestResult:
    return BacktestResult(
        run_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        engine_version="0.1.0",
        strategy_id="rsi",
        strategy_version="1.0.0",
        symbol="AAPL",
        timeframe="1d",
        range_start=datetime(2026, 1, 1, tzinfo=UTC),
        range_end=datetime(2026, 5, 1, tzinfo=UTC),
        bars_hash="deadbeef" * 8,
        params={"period": 14, "oversold": 30.0, "overbought": 70.0},
        costs={"commission_bps": 5.0, "slippage_bps": 5.0},
        initial_capital=10_000.0,
        sizing="fixed_fraction",
        started_at=datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 22, 10, 0, 1, tzinfo=UTC),
        trades=[
            Trade(
                entry_bar_index=5,
                exit_bar_index=12,
                entry_price=100.0,
                exit_price=110.0,
                kind="long",
            ),
            Trade(
                entry_bar_index=20,
                exit_bar_index=None,
                entry_price=120.0,
                exit_price=None,
                kind="long",
            ),
        ],
        equity_curve=[
            EquityPoint(ts=datetime(2026, 1, 1, tzinfo=UTC), equity=10_000.0),
            EquityPoint(ts=datetime(2026, 1, 2, tzinfo=UTC), equity=10_500.0),
            EquityPoint(ts=datetime(2026, 1, 3, tzinfo=UTC), equity=11_000.0),
        ],
        metrics=BacktestMetrics(
            total_return=0.10,
            sharpe=1.234,
            max_drawdown=-0.05,
            max_drawdown_duration_bars=3,
            win_rate=1.0,
            trade_count=1,
            buy_and_hold_return=0.08,
        ),
    )


def test_valid_payload_constructs() -> None:
    result = _hand_built_result()
    assert result.run_id == "a" * 32
    assert result.metrics.total_return == 0.10
    assert len(result.trades) == 2
    assert len(result.equity_curve) == 3


def test_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        BacktestResult(
            run_id="x" * 32,
            engine_version="0.1.0",
            strategy_id="rsi",
            strategy_version="1.0.0",
            symbol="AAPL",
            timeframe="1d",
            range_start=datetime(2026, 1, 1, tzinfo=UTC),
            range_end=datetime(2026, 5, 1, tzinfo=UTC),
            bars_hash="x" * 64,
            params={},
            costs={},
            initial_capital=10_000.0,
            sizing="fixed_fraction",
            started_at=datetime(2026, 5, 22, tzinfo=UTC),
            finished_at=datetime(2026, 5, 22, tzinfo=UTC),
            trades=[],
            equity_curve=[],
            metrics=BacktestMetrics(
                total_return=0.0,
                sharpe=0.0,
                max_drawdown=0.0,
                max_drawdown_duration_bars=0,
                win_rate=0.0,
                trade_count=0,
                buy_and_hold_return=0.0,
            ),
            extra_field="not allowed",  # type: ignore[call-arg]
        )


def test_json_round_trip_preserves_equality() -> None:
    original = _hand_built_result()
    dumped = original.model_dump(mode="json")
    reconstructed = BacktestResult(**dumped)
    assert reconstructed == original


def test_equity_point_extra_forbids() -> None:
    with pytest.raises(ValidationError):
        EquityPoint(
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            equity=10_000.0,
            extra="nope",  # type: ignore[call-arg]
        )


def test_metrics_extra_forbids() -> None:
    with pytest.raises(ValidationError):
        BacktestMetrics(
            total_return=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
            max_drawdown_duration_bars=0,
            win_rate=0.0,
            trade_count=0,
            buy_and_hold_return=0.0,
            extra="nope",  # type: ignore[call-arg]
        )
