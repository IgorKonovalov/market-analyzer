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
from market_analyser.advisor.models import Recommendation
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
    feature_set_id: str = "fs-v1",
    fallback_reason: str | None = None,
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
            feature_set_id=feature_set_id,
            training_cutoff=AS_OF,
            seed=7,
            lib_versions={"scikit-learn": "1.8.0"},
            fallback_reason=fallback_reason,
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

    def test_walk_forward_for_nonvoting_strategy_blocks_directional_call(self) -> None:
        """The backtested basis must back a strategy that actually voted the
        direction — an edge for a bystander strategy backs nothing."""

        alien_wf = make_walk_forward().model_copy(update={"strategy_id": "macd"})
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal("rsi", "long")],
            walk_forward=alien_wf,
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert any("not among the agreeing signals" in line for line in rec.rationale)

    def test_walk_forward_for_voting_strategy_still_directional(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal("rsi", "long"), make_signal("macd", "long")],
            walk_forward=make_walk_forward(),  # strategy_id "rsi" — among the voters
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"

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


class TestForecastTierInBasis:
    """Plan 0066 phase 2 (ADR-0057): the forecast basis carries the tier that
    backed the call (`feature_set_id`) and, when a richer tier was skipped, the
    stated `fallback_reason` — both on the recommendation itself, directional or
    flat, taken straight from the fused `ForecastResult`'s provenance."""

    _REASON = (
        "v2-full unavailable: 0 of 2746 bars survived the join (floor 500); "
        "trained v2-deep (1347 rows)"
    )

    def test_directional_basis_carries_tier_and_fallback(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(feature_set_id="fs-v2-deep", fallback_reason=self._REASON),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        assert rec.basis.forecast is not None
        assert rec.basis.forecast["feature_set_id"] == "fs-v2-deep"
        assert rec.basis.forecast["fallback_reason"] == self._REASON

    def test_flat_basis_carries_tier_and_fallback(self) -> None:
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
                feature_set_id="fs-v2-deep",
                fallback_reason=self._REASON,
            ),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert rec.basis.forecast is not None
        assert rec.basis.forecast["feature_set_id"] == "fs-v2-deep"
        assert rec.basis.forecast["fallback_reason"] == self._REASON

    def test_v2_full_run_reports_no_fallback_reason(self) -> None:
        """A genuine v2-full run has `fallback_reason=None` — the key is present
        in the basis dict but carries the honest null (nothing was skipped)."""

        rec = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(feature_set_id="fs-v2", fallback_reason=None),
            last_close=LAST_CLOSE,
        )
        assert rec.basis.forecast is not None
        assert rec.basis.forecast["feature_set_id"] == "fs-v2"
        assert rec.basis.forecast["fallback_reason"] is None


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

    def test_stale_forecast_as_of_raises(self) -> None:
        stale = make_forecast().model_copy(
            update={"as_of_bar_ts": datetime(2026, 5, 25, tzinfo=UTC)}
        )
        with pytest.raises(ValueError, match="inconsistent fusion inputs"):
            fuse(
                snapshot=make_snapshot(),
                signals=[make_signal()],
                walk_forward=make_walk_forward(),
                forecast=stale,
                last_close=LAST_CLOSE,
            )

    def test_stale_signal_as_of_raises(self) -> None:
        stale = make_signal().model_copy(
            update={"evaluated_through_ts": datetime(2026, 5, 25, tzinfo=UTC)}
        )
        with pytest.raises(ValueError, match="inconsistent fusion inputs"):
            fuse(
                snapshot=make_snapshot(),
                signals=[stale],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
            )


def _replay_direction(rec: Recommendation) -> str:
    """Recompute the verdict from the trace ALONE (Plan 0063's replayability
    claim): flat if any check failed; otherwise the direction is the agreement
    gate's actual value."""

    checks = rec.basis.checks
    assert checks  # every verdict carries a non-empty trace
    if not all(check.passed for check in checks):
        return "flat"
    agreement = next(
        check
        for check in checks
        if check.check == "live direction agrees with the forecast direction"
    )
    assert isinstance(agreement.actual, str)
    return agreement.actual


class TestFusionTrace:
    """Plan 0063 phase 2 done-when: `basis.checks` is a complete, ordered,
    numeric trace of every gate — replayable to the same verdict."""

    def test_directional_trace_is_the_exact_pinned_gate_list(self) -> None:
        """The full trace of the canonical directional case, spot-pinned
        against hand-computed fixture values — real numbers, not just
        non-null — in the fixed deterministic order."""

        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        snapshot = make_snapshot()
        conditions_fact = (
            f"trend={snapshot.trend}, momentum={snapshot.momentum}, volume={snapshot.volume_stance}"
        )
        assert [
            (check.leg, check.check, check.threshold, check.actual, check.passed)
            for check in rec.basis.checks
        ] == [
            ("alignment", "inputs share symbol/timeframe", "BTC-USD/1d", "BTC-USD/1d", True),
            (
                "alignment",
                "inputs share the as-of bar",
                AS_OF.isoformat(),
                AS_OF.isoformat(),
                True,
            ),
            ("conditions", "condition snapshot read", None, conditions_fact, True),
            (
                "forecast",
                "probabilities shipped (baseline beaten out-of-sample)",
                True,
                True,
                True,
            ),
            ("forecast", "argmax direction is directional", "long or short", "long", True),
            ("forecast", "calibrated P(direction)", None, 0.60, True),
            ("signal", "live vote: rsi", None, "long", True),
            ("signal", "no conflicting live votes", False, False, True),
            ("signal", "at least one directional live vote", "long or short", "long", True),
            ("signal", "live direction agrees with the forecast direction", "long", "long", True),
            ("backtest", "walk-forward basis supplied", True, True, True),
            ("backtest", "backtested edge positive (sharpe_mean > 0)", 0.0, 0.8, True),
            ("backtest", "walk-forward strategy among the agreeing votes", "rsi", "rsi", True),
        ]

    def test_directional_verdict_replays_from_the_trace_alone(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        assert all(check.passed for check in rec.basis.checks)
        assert _replay_direction(rec) == "long"

    def test_one_blocker_flat_replays_and_carries_the_failing_numbers(self) -> None:
        """A single failed leg (non-positive edge): exactly that gate fails,
        with the real sharpe_mean as its actual — the numeric superset of the
        one blocker string."""

        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(sharpe_mean=-0.4),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        failed = [check for check in rec.basis.checks if not check.passed]
        assert [(check.leg, check.check, check.threshold, check.actual) for check in failed] == [
            ("backtest", "backtested edge positive (sharpe_mean > 0)", 0.0, -0.4),
        ]
        assert any("no backtested edge" in line for line in rec.rationale)
        assert _replay_direction(rec) == "flat"

    def test_all_legs_fail_flat_replays_with_every_gate_failed(self) -> None:
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
        failed = {check.check for check in rec.basis.checks if not check.passed}
        assert failed == {
            "probabilities shipped (baseline beaten out-of-sample)",
            "argmax direction is directional",
            "calibrated P(direction)",
            "at least one directional live vote",
            "live direction agrees with the forecast direction",
            "walk-forward basis supplied",
            "backtested edge positive (sharpe_mean > 0)",
            "walk-forward strategy among the agreeing votes",
        }
        assert _replay_direction(rec) == "flat"

    def test_conflicting_votes_fail_the_conflict_gate_with_each_vote_recorded(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal("rsi", "long"), make_signal("macd", "short")],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        votes = [
            (check.check, check.actual)
            for check in rec.basis.checks
            if check.check.startswith("live vote: ")
        ]
        assert votes == [("live vote: macd", "short"), ("live vote: rsi", "long")]
        conflict_gate = next(
            check for check in rec.basis.checks if check.check == "no conflicting live votes"
        )
        assert conflict_gate.actual is True and conflict_gate.passed is False
        assert _replay_direction(rec) == "flat"

    def test_trace_order_is_deterministic_across_runs(self) -> None:
        def run() -> list[dict[str, object]]:
            return [
                check.model_dump(mode="json")
                for check in fuse(
                    snapshot=make_snapshot(),
                    signals=[make_signal(), make_signal("macd", "long", fresh=False)],
                    walk_forward=make_walk_forward(sharpe_mean=-0.4),
                    forecast=make_forecast(),
                    last_close=LAST_CLOSE,
                ).basis.checks
            ]

        assert run() == run()


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
