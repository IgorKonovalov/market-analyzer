"""Fusion engine tests (Plan 0038 phase 1).

Pins the phase's done-when: aligned fixture inputs produce a directional
`Recommendation` with rationale + full basis; disagreement or a missing edge
on any leg produces an honest flat; the fusion is deterministic; conviction
is a monotone function of the forecast probability and the backtested edge
(the plan's open question, guarded here); and the advisor package imports
only analyst *outputs*, never their internals.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

import market_analyser.advisor
from market_analyser.advisor.fusion import fuse
from market_analyser.analysis.types import (
    ConditionSnapshot,
    Level,
    MomentumStance,
    Trend,
    VolumeStance,
)
from market_analyser.backtest.result import BacktestMetrics
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.forecast.result import (
    EdgeStrength,
    ForecastProvenance,
    ForecastResult,
)
from market_analyser.forecast.validation import ForecastValidation

SYMBOL = "BTC-USD"
TIMEFRAME = "1d"
AS_OF = datetime(2026, 6, 1, tzinfo=UTC)
LAST_CLOSE = 100.0
ATR = 2.0


def make_level(price: float, role: Literal["support", "resistance"]) -> Level:
    return Level(
        price=price,
        role=role,
        touches=3,
        volume_at_level=1000.0,
        strength=0.8,
        first_ts=datetime(2026, 5, 1, tzinfo=UTC),
        last_ts=AS_OF,
    )


def make_snapshot(
    *,
    trend: Trend = Trend.UP,
    atr: float | None = ATR,
    nearest_support: Level | None = None,
    nearest_resistance: Level | None = None,
) -> ConditionSnapshot:
    return ConditionSnapshot(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of=AS_OF,
        trend=trend,
        momentum=MomentumStance.BULLISH,
        volume_stance=VolumeStance.NORMAL,
        indicators={"rsi": 58.0, "atr": atr},
        support_resistance={"support": [95.0], "resistance": [108.0]},
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        recent_patterns=[],
        active_patterns=[],
    )


def make_signal(
    strategy_id: str = "rsi",
    position: Literal["flat", "long", "short"] = "long",
    *,
    fresh: bool = True,
) -> SignalEvaluation:
    return SignalEvaluation(
        strategy_id=strategy_id,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        evaluated_through_ts=AS_OF,
        closed_bar_count=200,
        latest_bar_excluded_as_forming=False,
        current_position=position,
        fresh_signal=fresh,
    )


def make_walk_forward(*, sharpe_mean: float | None = 0.8) -> WalkForwardResult:
    metrics = BacktestMetrics(
        total_return=0.2,
        sharpe=1.1,
        max_drawdown=-0.1,
        max_drawdown_duration_bars=12,
        win_rate=0.6,
        trade_count=20,
        buy_and_hold_return=0.15,
    )
    return WalkForwardResult(
        strategy_id="rsi",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        n_splits=5,
        folds=[],
        aggregate={
            "total_return_mean": 0.04,
            "total_return_std": 0.01,
            "sharpe_mean": sharpe_mean,
            "sharpe_std": 0.2,
        },
        full_run_baseline=metrics,
    )


def make_forecast(
    *,
    prob_up: float | None = 0.60,
    prob_down: float | None = 0.25,
    prob_flat: float | None = 0.15,
    beats_baseline: bool = True,
    edge_strength: EdgeStrength = "clear",
) -> ForecastResult:
    return ForecastResult(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_bar_ts=AS_OF,
        horizon_bars=1,
        prob_up=prob_up,
        prob_down=prob_down,
        prob_flat=prob_flat,
        validation=ForecastValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=4,
            skill=0.55 if beats_baseline else 0.48,
            baseline_skill=0.50,
            persistence_skill=0.50,
            majority_skill=0.47,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=ForecastProvenance(
            model_version="abc123",
            feature_set_id="fs-v1",
            training_cutoff=AS_OF,
            seed=7,
            lib_versions={"scikit-learn": "1.8.0"},
        ),
        edge_margin=0.05 if beats_baseline else -0.02,
        edge_strength=edge_strength,
    )


class TestDirectionalCall:
    def test_aligned_inputs_produce_long_with_full_basis(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        assert rec.label == "advisory"
        assert rec.rationale  # non-empty, human-readable "why"
        assert rec.basis.backtest is not None
        assert rec.basis.backtest["sharpe_mean"] == 0.8
        assert rec.basis.forecast is not None
        assert rec.basis.forecast["model_version"] == "abc123"
        assert rec.basis.conditions and rec.basis.signals
        assert rec.as_of_bar_ts == AS_OF

    def test_long_level_geometry(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.entry_zone == (99.5, 100.5)  # close +/- 0.25*ATR
        assert rec.stop == pytest.approx(94.8)  # support - 0.1*ATR buffer
        assert rec.targets == [108.0]  # nearest resistance
        assert rec.stop is not None and rec.entry_zone is not None
        assert rec.stop < rec.entry_zone[0] < rec.entry_zone[1] < rec.targets[0]

    def test_short_call_mirrors_geometry(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal(position="short")],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(prob_up=0.25, prob_down=0.60),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "short"
        assert rec.stop == pytest.approx(108.2)  # resistance + 0.1*ATR buffer
        assert rec.targets == [95.0]  # nearest support
        assert rec.stop is not None and rec.entry_zone is not None
        assert rec.targets[0] < rec.entry_zone[0] < rec.entry_zone[1] < rec.stop

    def test_atr_fallback_levels_without_sr(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.stop == pytest.approx(96.0)  # close - 2*ATR
        assert rec.targets == [pytest.approx(104.0)]  # close + 2*ATR

    def test_determinism_identical_inputs_identical_recommendation(self) -> None:
        def run() -> dict[str, object]:
            return fuse(
                snapshot=make_snapshot(nearest_support=make_level(95.0, "support")),
                signals=[make_signal(), make_signal("macd", "long", fresh=False)],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
            ).model_dump(mode="json")

        assert run() == run()


class TestConvictionMapping:
    """Conviction = P(direction) * clamp(sharpe_mean, 0, 1) — monotone, derived."""

    def _conviction(self, *, prob_up: float, sharpe_mean: float) -> float:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(sharpe_mean=sharpe_mean),
            forecast=make_forecast(prob_up=prob_up, prob_down=1.0 - prob_up - 0.15),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        return rec.conviction

    def test_conviction_moves_with_forecast_probability(self) -> None:
        low = self._conviction(prob_up=0.50, sharpe_mean=0.8)
        high = self._conviction(prob_up=0.70, sharpe_mean=0.8)
        assert high > low  # not a constant — maps from the forecast

    def test_conviction_moves_with_backtested_edge(self) -> None:
        thin = self._conviction(prob_up=0.60, sharpe_mean=0.3)
        strong = self._conviction(prob_up=0.60, sharpe_mean=0.9)
        assert strong > thin  # maps from the walk-forward edge

    def test_conviction_is_the_documented_product(self) -> None:
        assert self._conviction(prob_up=0.60, sharpe_mean=0.5) == pytest.approx(0.30)

    def test_edge_credit_saturates_at_full_credit(self) -> None:
        at_one = self._conviction(prob_up=0.60, sharpe_mean=1.0)
        beyond = self._conviction(prob_up=0.60, sharpe_mean=2.5)
        assert at_one == beyond == pytest.approx(0.60)


class TestFlatVerdicts:
    def test_no_edge_forecast_yields_flat(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(
                prob_up=None,
                prob_down=None,
                prob_flat=None,
                beats_baseline=False,
                edge_strength="no_edge",
            ),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert rec.conviction == 0.0
        assert rec.entry_zone is None and rec.stop is None and rec.targets == []
        assert any("no edge over baseline" in line for line in rec.rationale)

    def test_conflicting_signals_yield_flat(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal("rsi", "long"), make_signal("macd", "short")],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert any("conflict" in line for line in rec.rationale)

    def test_signal_disagreeing_with_forecast_yields_flat(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal(position="short")],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),  # forecast says long
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert any("disagree" in line for line in rec.rationale)

    def test_missing_walk_forward_blocks_directional_call(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=None,
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert any("walk-forward" in line for line in rec.rationale)

    def test_nonpositive_backtested_edge_blocks_directional_call(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(sharpe_mean=-0.4),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert any("no backtested edge" in line for line in rec.rationale)

    def test_flat_still_carries_a_basis(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[],
            walk_forward=None,
            forecast=make_forecast(
                prob_up=None,
                prob_down=None,
                prob_flat=None,
                beats_baseline=False,
                edge_strength="no_edge",
            ),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert rec.basis.conditions  # never a groundless artifact, even flat


class TestInputValidation:
    def test_inconsistent_symbol_raises(self) -> None:
        alien = make_signal().model_copy(update={"symbol": "ETH-USD"})
        with pytest.raises(ValueError, match="inconsistent fusion inputs"):
            fuse(
                snapshot=make_snapshot(),
                signals=[alien],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
            )

    def test_nonpositive_last_close_raises(self) -> None:
        with pytest.raises(ValueError, match="last_close"):
            fuse(
                snapshot=make_snapshot(),
                signals=[make_signal()],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=0.0,
            )


def test_advisor_imports_only_analyst_outputs() -> None:
    """Import-lint (phase 1 done-when): the advisor consumes analyst *output*
    models through their stable surfaces and never reaches into analysis /
    strategy / backtest / forecast internals."""

    allowed_prefixes = (
        "__future__",
        "collections",
        "datetime",
        "typing",
        "pydantic",
        "market_analyser.advisor",
        "market_analyser.analysis.types",
        "market_analyser.backtest.types",
        "market_analyser.backtest.walk_forward_types",
        "market_analyser.forecast",
    )
    package_file = market_analyser.advisor.__file__
    assert package_file is not None
    advisor_dir = Path(package_file).parent
    checked = 0
    for source in sorted(advisor_dir.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                checked += 1
                assert name.startswith(allowed_prefixes), (
                    f"{source.name} imports {name!r} — the advisor may only "
                    "consume analyst output surfaces (Plan 0038 phase 1)"
                )
    assert checked > 0  # the lint actually saw imports
