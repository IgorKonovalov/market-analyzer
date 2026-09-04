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
from market_analyser.advisor.fusion import (
    DIRECTION_SKILL_MARGIN,
    REFERENCE_VOL,
    SIZE_CAP,
    SIZE_FLOOR,
    STOP_VOL_CAP_FRACTION,
    fuse,
)
from market_analyser.advisor.models import Recommendation
from market_analyser.analysis.types import (
    ConditionSnapshot,
    Level,
    MarketStructure,
    MomentumStance,
    PatternHit,
    Trend,
    VolumeStance,
)
from market_analyser.backtest.result import BacktestMetrics
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.forecast.regime import (
    RegimeForecast,
    RegimeState,
    RegimeValidation,
)
from market_analyser.forecast.result import (
    EdgeStrength,
    ForecastProvenance,
    ForecastResult,
)
from market_analyser.forecast.validation import ForecastValidation
from market_analyser.forecast.volatility import (
    BaselineKind,
    VolatilityForecast,
    VolatilityValidation,
)

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
        recent_divergences=[],
        market_structure=MarketStructure(structural_trend="range", labeled_pivots=[], events=[]),
        nearest_fib_level=None,
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
    edge_margin: float | None = None,
) -> ForecastResult:
    # A clear edge (0.05) clears DIRECTION_SKILL_MARGIN so the leg gates by
    # default; callers pass `edge_margin` to exercise the demotion boundary.
    default_margin = 0.05 if beats_baseline else -0.02
    resolved_margin = edge_margin if edge_margin is not None else default_margin
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
        edge_margin=resolved_margin,
        edge_strength=edge_strength,
    )


def make_volatility(
    *,
    predicted_vol: float | None = 0.04,
    baseline_vol: float | None = 0.03,
    beats_baseline: bool = True,
    baseline_kind: BaselineKind = "ewma",
) -> VolatilityForecast:
    """A volatility forecast fixture (Plan 0077 phase 5). Defaults to a trusted
    model reading above the reference vol, so it shrinks size and widens the stop."""

    return VolatilityForecast(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_bar_ts=AS_OF,
        horizon_bars=1,
        predicted_vol=predicted_vol,
        band=(predicted_vol * 0.8, predicted_vol * 1.2) if predicted_vol is not None else None,
        baseline_vol=baseline_vol,
        baseline_kind=baseline_kind,
        beats_baseline=beats_baseline,
        score_margin=0.1 if beats_baseline else -0.1,
        validation=VolatilityValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=100,
            model_qlike=1.0 if beats_baseline else 1.3,
            baseline_qlike=1.1,
            baseline_kind=baseline_kind,
            persistence_qlike=1.2,
            ewma_qlike=1.1,
            score_margin=0.1 if beats_baseline else -0.2,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=None,
    )


def make_regime(
    *,
    current: RegimeState = RegimeState.UP_QUIET,
    p_stay: float = 0.9,
    beats_baseline: bool = True,
) -> RegimeForecast:
    """A regime-transition forecast fixture (Plan 0077 phase 5). `p_stay` is the
    probability the current regime persists; a trusted low `p_stay` softens
    conviction. Defaults to a trusted, sticky (high `p_stay`) regime → neutral."""

    others = [s for s in RegimeState if s != current]
    probs = {s: (1.0 - p_stay) / len(others) for s in others}
    probs[current] = p_stay
    return RegimeForecast(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_bar_ts=AS_OF,
        horizon_bars=1,
        current_regime=current,
        transition_probs=probs,
        beats_baseline=beats_baseline,
        score_margin=0.05 if beats_baseline else -0.05,
        validation=RegimeValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=100,
            model_brier=0.5 if beats_baseline else 0.7,
            persistence_brier=0.6,
            score_margin=0.1 if beats_baseline else -0.1,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=None,
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
    """Conviction is derived, never invented — and has two branches (ADR-0071).

    Gating direction leg:  conviction = P(direction) * edge_credit * regime_factor
    Demoted direction leg: conviction =                edge_credit * regime_factor

    where ``edge_credit = clamp(sharpe_mean / SHARPE_FULL_CREDIT, 0, 1)``. The
    demoted branch is the common production shape — the direction forecaster
    rarely beats baseline (ADR-0070) — so both branches are pinned here.
    """

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

    def test_gating_leg_conviction_is_the_documented_product(self) -> None:
        """The gating branch only: P(direction) is a factor because the leg
        shipped one. See the demoted-branch tests below for the other case."""
        assert self._conviction(prob_up=0.60, sharpe_mean=0.5) == pytest.approx(0.30)

    def test_edge_credit_saturates_at_full_credit(self) -> None:
        at_one = self._conviction(prob_up=0.60, sharpe_mean=1.0)
        beyond = self._conviction(prob_up=0.60, sharpe_mean=2.5)
        assert at_one == beyond == pytest.approx(0.60)

    def _demoted_conviction(self, *, sharpe_mean: float) -> float:
        """Conviction on the demoted branch: the direction leg ships no
        probability at all (`prob_*` None, `no_edge`) — the shape every live
        `recommend` call returned in the 2026-09-04 BTC-USD sweep."""

        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(sharpe_mean=sharpe_mean),
            forecast=make_forecast(
                prob_up=None,
                prob_down=None,
                prob_flat=None,
                beats_baseline=False,
                edge_strength="no_edge",
            ),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"  # corroborated by signal + backtest
        assert rec.direction_leg is not None and rec.direction_leg.gating is False
        return rec.conviction

    def test_demoted_leg_conviction_is_the_edge_credit_alone(self) -> None:
        """ADR-0071: with no shipped probability the P(direction) factor drops out
        entirely — conviction *is* the clamped walk-forward edge credit. Regresses
        if the mapping ever applies P unconditionally."""

        assert self._demoted_conviction(sharpe_mean=0.5) == pytest.approx(0.5)
        assert self._demoted_conviction(sharpe_mean=0.87) == pytest.approx(0.87)

    def test_demoted_conviction_saturates_at_full_credit_not_certainty(self) -> None:
        """A demoted conviction of 1.0 means `sharpe_mean >= SHARPE_FULL_CREDIT`,
        not a probability of being right — the misreading Plan 0114 came from."""

        assert self._demoted_conviction(sharpe_mean=2.5) == pytest.approx(1.0)

    def test_demoted_branch_is_not_the_gating_product(self) -> None:
        """The two branches are not interchangeable: at the same backtested edge
        the demoted conviction is strictly larger, because a shipped P < 1 always
        shrinks the gating product."""

        assert self._demoted_conviction(sharpe_mean=0.5) > self._conviction(
            prob_up=0.60, sharpe_mean=0.5
        )


class TestFlatVerdicts:
    def test_no_edge_forecast_alone_no_longer_vetoes_a_corroborated_call(self) -> None:
        """Plan 0077 phase 5 done-when (b): a no-edge (demoted) direction forecast
        no longer vetoes a call the voting legs corroborate — it is advisory, not
        gating. (Pre-0077 this exact input was flat; that veto is the bug ADR-0071
        fixes.) The demotion is recorded in the trace and on `direction_leg`."""

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
        assert rec.direction == "long"  # corroborated by signal + backtest, not vetoed
        assert rec.direction_leg is not None
        assert rec.direction_leg.present is True and rec.direction_leg.gating is False
        # The four direction-leg checks are recorded but non-gating.
        forecast_checks = [c for c in rec.basis.checks if c.leg == "forecast"]
        assert forecast_checks and all(not c.gating for c in forecast_checks)
        agreement = next(
            c for c in rec.basis.checks if c.check.endswith("agrees with the forecast direction")
        )
        assert agreement.gating is False
        assert any("non-gating" in line for line in rec.rationale)

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


class TestDirectionDemotion:
    """Plan 0077 phase 5 (ADR-0071): the direction leg gates only when its
    out-of-sample skill margin clears `DIRECTION_SKILL_MARGIN`; below it the leg
    is advisory — it neither vetoes nor solely decides a call."""

    def test_at_threshold_the_leg_gates_and_votes_as_before(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(edge_margin=DIRECTION_SKILL_MARGIN),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        assert rec.direction_leg is not None and rec.direction_leg.gating is True
        assert all(c.gating for c in rec.basis.checks if c.leg == "forecast")
        assert rec.rationale[0].startswith("forecast: P(long)=")

    def test_just_below_threshold_the_leg_is_demoted(self) -> None:
        margin = DIRECTION_SKILL_MARGIN - 1e-6
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(edge_margin=margin),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"  # still corroborated by signal + backtest
        assert rec.direction_leg is not None and rec.direction_leg.gating is False
        assert rec.direction_leg.skill_margin == pytest.approx(margin)
        assert rec.rationale[0].startswith("direction forecast leg present but non-gating")

    def test_gating_forecast_disagreement_still_vetoes(self) -> None:
        """Done-when (c): above the threshold the leg votes as before — a live
        signal disagreeing with the gating forecast still blocks the call."""

        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal(position="short")],
            walk_forward=make_walk_forward(),  # rsi
            forecast=make_forecast(edge_margin=DIRECTION_SKILL_MARGIN),  # long, gating
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert any("disagree" in line for line in rec.rationale)

    def test_demoted_forecast_disagreement_no_longer_vetoes(self) -> None:
        """Done-when (b): a demoted forecast leaning one way cannot veto a call
        the voting legs corroborate the other way — the signal + backtest carry
        the short, the advisory-long forecast is recorded but non-gating."""

        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal(position="short")],
            walk_forward=make_walk_forward(),  # rsi, positive → among the short voters
            forecast=make_forecast(edge_margin=0.01),  # argmax long, but demoted
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "short"
        assert rec.direction_leg is not None and rec.direction_leg.gating is False

    def test_demotion_is_deterministic(self) -> None:
        def run() -> dict[str, object]:
            return fuse(
                snapshot=make_snapshot(nearest_support=make_level(95.0, "support")),
                signals=[make_signal()],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(
                    edge_margin=0.01,
                    prob_up=None,
                    prob_down=None,
                    prob_flat=None,
                    beats_baseline=False,
                    edge_strength="no_edge",
                ),
                last_close=LAST_CLOSE,
            ).model_dump(mode="json")

        assert run() == run()


class TestNonVotingVolatility:
    """Plan 0077 phase 5 (ADR-0071): volatility is a non-voting input — it shapes
    the inverse-vol size hint and widens the stop, never a direction."""

    def _long_with_sr(self, **vol_kwargs: object) -> Recommendation:
        return fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
            **vol_kwargs,  # type: ignore[arg-type]
        )

    def test_high_vol_shrinks_size_and_widens_stop(self) -> None:
        base = self._long_with_sr()
        hot = self._long_with_sr(
            volatility=make_volatility(predicted_vol=0.10, beats_baseline=True)
        )
        assert hot.direction == "long"
        assert hot.sizing is not None and hot.sizing.vol_source == "model"
        assert hot.sizing.size_factor < 1.0  # inverse-vol: high vol → smaller
        assert base.stop is not None and hot.stop is not None
        assert hot.stop < base.stop  # long: a wider stop sits lower
        assert hot.sizing.stop_vol_distance is not None

    def test_low_vol_grows_size_up_to_the_cap(self) -> None:
        rec = self._long_with_sr(
            volatility=make_volatility(predicted_vol=0.005, beats_baseline=True)
        )
        assert rec.sizing is not None
        assert rec.sizing.size_factor == pytest.approx(SIZE_CAP)  # 0.03/0.005 clamped

    def test_untrained_model_falls_back_to_the_baseline_reading(self) -> None:
        rec = self._long_with_sr(
            volatility=make_volatility(predicted_vol=None, baseline_vol=0.03, beats_baseline=False)
        )
        assert rec.sizing is not None and rec.sizing.vol_source == "baseline"
        assert rec.sizing.vol_used == 0.03
        assert rec.sizing.size_factor == pytest.approx(REFERENCE_VOL / 0.03)

    def test_no_usable_vol_reading_is_neutral(self) -> None:
        rec = self._long_with_sr(
            volatility=make_volatility(predicted_vol=None, baseline_vol=None, beats_baseline=False)
        )
        assert rec.sizing is not None and rec.sizing.vol_source == "none"
        assert rec.sizing.size_factor == 1.0
        assert rec.sizing.stop_vol_distance is None

    def test_degenerate_vol_cannot_blow_up_size_or_stop(self) -> None:
        """Done-when (d): an absurd predicted vol produces bounded sizing and a
        capped stop distance — never a dangerous number."""

        rec = self._long_with_sr(
            volatility=make_volatility(predicted_vol=99.0, beats_baseline=True)
        )
        assert rec.sizing is not None
        assert rec.sizing.size_factor == pytest.approx(SIZE_FLOOR)  # clamped floor
        assert rec.sizing.stop_vol_distance is not None
        assert rec.sizing.stop_vol_distance <= STOP_VOL_CAP_FRACTION * LAST_CLOSE

    def test_no_volatility_input_leaves_sizing_absent(self) -> None:
        rec = self._long_with_sr()
        assert rec.sizing is None


class TestNonVotingRegime:
    """Plan 0077 phase 5 (ADR-0071): regime feeds conviction only — an unstable,
    trusted regime softens it; an untrusted regime is neutral; never a direction."""

    def _long(self, **regime_kwargs: object) -> Recommendation:
        return fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
            **regime_kwargs,  # type: ignore[arg-type]
        )

    def test_unstable_trusted_regime_lowers_conviction(self) -> None:
        stable = self._long(regime=make_regime(p_stay=0.95, beats_baseline=True))
        unstable = self._long(regime=make_regime(p_stay=0.30, beats_baseline=True))
        assert unstable.direction == stable.direction == "long"
        assert unstable.conviction < stable.conviction
        assert unstable.regime_context is not None and stable.regime_context is not None
        assert unstable.regime_context.conviction_factor < stable.regime_context.conviction_factor

    def test_untrusted_regime_is_neutral(self) -> None:
        base = self._long()  # no regime
        untrusted = self._long(regime=make_regime(p_stay=0.2, beats_baseline=False))
        assert untrusted.regime_context is not None and untrusted.regime_context.trusted is False
        assert untrusted.regime_context.conviction_factor == 1.0
        assert untrusted.conviction == pytest.approx(base.conviction)

    def test_regime_context_records_the_current_regime(self) -> None:
        rec = self._long(regime=make_regime(current=RegimeState.DOWN_VOLATILE, beats_baseline=True))
        assert rec.regime_context is not None
        assert rec.regime_context.current_regime == "down_volatile"


class TestNonVotingNeverFlipsDirection:
    """Plan 0077 phase 5 done-when (a): vol/regime can never flip or manufacture a
    direction — an adverse forecast makes a call smaller, wider-stopped, and
    lower-conviction, never short; and they cannot conjure a call from a flat."""

    def test_adverse_vol_and_regime_keep_a_long_long(self) -> None:
        plain = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        adverse = fuse(
            snapshot=make_snapshot(
                nearest_support=make_level(95.0, "support"),
                nearest_resistance=make_level(108.0, "resistance"),
            ),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
            volatility=make_volatility(predicted_vol=0.12, beats_baseline=True),
            regime=make_regime(p_stay=0.2, beats_baseline=True),
        )
        assert plain.direction == adverse.direction == "long"  # never flips to short
        assert adverse.sizing is not None and adverse.sizing.size_factor < 1.0  # smaller
        assert plain.stop is not None and adverse.stop is not None
        assert adverse.stop < plain.stop  # wider-stopped
        assert adverse.conviction < plain.conviction  # lower conviction

    def test_vol_regime_cannot_manufacture_a_call_from_flat(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal(position="flat")],  # no directional vote
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
            volatility=make_volatility(predicted_vol=0.005, beats_baseline=True),
            regime=make_regime(p_stay=0.99, beats_baseline=True),
        )
        assert rec.direction == "flat"
        assert rec.sizing is None and rec.regime_context is None

    def test_non_voting_inputs_recorded_in_the_trace(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
            volatility=make_volatility(),
            regime=make_regime(),
        )
        vol_row = next(c for c in rec.basis.checks if c.check.startswith("volatility forecast"))
        reg_row = next(c for c in rec.basis.checks if c.check.startswith("regime forecast"))
        assert vol_row.gating is False and vol_row.passed is True
        assert reg_row.gating is False and reg_row.passed is True

    def test_inconsistent_volatility_symbol_raises(self) -> None:
        alien = make_volatility().model_copy(update={"symbol": "ETH-USD"})
        with pytest.raises(ValueError, match="inconsistent fusion inputs"):
            fuse(
                snapshot=make_snapshot(),
                signals=[make_signal()],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
                volatility=alien,
            )

    def test_determinism_with_non_voting_inputs(self) -> None:
        def run() -> dict[str, object]:
            return fuse(
                snapshot=make_snapshot(nearest_support=make_level(95.0, "support")),
                signals=[make_signal()],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
                volatility=make_volatility(),
                regime=make_regime(),
            ).model_dump(mode="json")

        assert run() == run()


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
    claim, refined for Plan 0077 phase 5): flat if any *gating* check failed;
    otherwise the direction is the live directional-vote gate's actual value —
    which equals the called direction whether the forecast leg gated (agreement
    proved forecast_dir == signal_dir) or was demoted (the call rests on the
    signal). Non-gating checks are recorded but never block."""

    checks = rec.basis.checks
    assert checks  # every verdict carries a non-empty trace
    if not all(check.passed for check in checks if check.gating):
        return "flat"
    vote = next(check for check in checks if check.check == "at least one directional live vote")
    assert isinstance(vote.actual, str)
    return vote.actual


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


class TestReasonCodes:
    """Plan 0069 phase 4 done-when: `reason_codes` is a parallel, translatable
    surface — one code per rationale/blocker line (1:1 with `rationale`), then
    one per gate (1:1 with `basis.checks`, same order) — additive to the
    unchanged English prose; directional exactly when every gate passed."""

    def test_directional_reason_codes_cover_rationale_then_gates(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "long"
        assert len(rec.reason_codes) == len(rec.rationale) + len(rec.basis.checks)
        rationale_codes = rec.reason_codes[: len(rec.rationale)]
        gate_codes = rec.reason_codes[len(rec.rationale) :]
        assert [c.code for c in rationale_codes] == [
            "reason.forecast",
            "reason.signals_agree",
            "reason.backtested_edge",
            "reason.conditions",
        ]
        assert [c.code for c in gate_codes] == [
            "gate.alignment_scope",
            "gate.alignment_asof",
            "gate.conditions_read",
            "gate.forecast_probs_shipped",
            "gate.forecast_argmax_directional",
            "gate.forecast_calibrated_prob",
            "gate.signal_live_vote",
            "gate.signal_no_conflict",
            "gate.signal_directional_vote",
            "gate.signal_agrees_forecast",
            "gate.backtest_basis_supplied",
            "gate.backtest_edge_positive",
            "gate.backtest_strategy_agrees",
        ]
        # The per-strategy vote code carries its strategy_id; numeric forecast
        # params ride raw (the renderer formats them en-US, ADR-0063).
        assert gate_codes[6].params == {"strategy_id": "rsi"}
        forecast_code = rationale_codes[0]
        assert forecast_code.params["direction"] == "long"
        assert forecast_code.params["prob"] == 0.60
        assert forecast_code.params["horizon_bars"] == 1
        assert forecast_code.params["edge_strength"] == "clear"

    def test_english_prose_is_unchanged_reason_codes_are_additive(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        # The agent/MCP-facing English prose is untouched by the additive codes.
        assert rec.rationale[0].startswith("forecast: P(long)=")
        assert rec.rationale[-1].startswith("conditions: trend=")

    def test_flat_reason_codes_cover_header_blockers_then_gates(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal()],
            walk_forward=make_walk_forward(sharpe_mean=-0.4),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        assert len(rec.reason_codes) == len(rec.rationale) + len(rec.basis.checks)
        rationale_codes = rec.reason_codes[: len(rec.rationale)]
        assert [c.code for c in rationale_codes] == [
            "reason.no_actionable_edge",
            "blocker.no_backtested_edge",
        ]
        # The blocker code carries the failing sharpe_mean as a raw number.
        assert rationale_codes[1].params == {"sharpe_mean": -0.4}

    def test_conflict_blocker_code_carries_the_conflicting_ids(self) -> None:
        rec = fuse(
            snapshot=make_snapshot(),
            signals=[make_signal("rsi", "long"), make_signal("macd", "short")],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        )
        assert rec.direction == "flat"
        conflict = next(c for c in rec.reason_codes if c.code == "blocker.signals_conflict")
        assert conflict.params == {"long": "rsi", "short": "macd"}

    def test_invariant_gate_codes_align_and_directional_iff_all_gates_passed(self) -> None:
        """Mirrors the 0063 directional ⟺ every-check-passed invariant on the
        reason-code surface: the gate portion is 1:1 with `basis.checks` on every
        verdict, and directional exactly when every gate passed."""

        def assert_invariant(rec: Recommendation, *, expected_directional: bool) -> None:
            gate_codes = rec.reason_codes[len(rec.rationale) :]
            assert len(gate_codes) == len(rec.basis.checks)
            # Refined for Plan 0077 phase 5: directional iff every *gating* check
            # passed (a demoted leg's failed checks are non-gating, not blockers).
            all_gating_passed = all(check.passed for check in rec.basis.checks if check.gating)
            assert all_gating_passed == (rec.direction != "flat") == expected_directional

        assert_invariant(
            fuse(
                snapshot=make_snapshot(),
                signals=[make_signal()],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
            ),
            expected_directional=True,
        )
        assert_invariant(
            fuse(
                snapshot=make_snapshot(),
                signals=[make_signal(position="short")],  # disagrees with the forecast
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
            ),
            expected_directional=False,
        )
        assert_invariant(
            fuse(
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
            ),
            expected_directional=False,
        )

    def test_reason_codes_are_deterministic(self) -> None:
        def run() -> list[dict[str, object]]:
            return [
                code.model_dump(mode="json")
                for code in fuse(
                    snapshot=make_snapshot(),
                    signals=[make_signal(), make_signal("macd", "long", fresh=False)],
                    walk_forward=make_walk_forward(),
                    forecast=make_forecast(),
                    last_close=LAST_CLOSE,
                ).reason_codes
            ]

        assert run() == run()


class TestConditionSignalCodes:
    """Plan 0069 phase 4b done-when: `basis.condition_codes`/`signal_codes` are
    the translatable mirrors of the `conditions`/`signals` prose — one code per
    line, 1:1 and in the same order, each condition/signal enum value carried as
    a raw token in `params` — additive to the unchanged English prose lists."""

    def test_condition_codes_mirror_the_conditions_prose(self) -> None:
        hit = PatternHit(
            bar_index=199, pattern="hammer", direction="bullish", strength=0.8, span_bars=1
        )
        snapshot = make_snapshot().model_copy(
            update={
                "momentum": MomentumStance.OVERSOLD,
                "volume_stance": VolumeStance.HEAVY,
                "recent_patterns": [hit],
            }
        )
        basis = fuse(
            snapshot=snapshot,
            signals=[make_signal()],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        ).basis
        # 1:1 with the prose, same order (trend, momentum, volume, then one per
        # recent candlestick pattern).
        assert len(basis.condition_codes) == len(basis.conditions)
        assert [c.code for c in basis.condition_codes] == [
            "condition.trend",
            "condition.momentum",
            "condition.volume",
            "condition.candlestick",
        ]
        # Enum values ride as raw tokens the renderer localizes via an enum-label
        # catalog (ADR-0063) — never the English prose word.
        assert basis.condition_codes[0].params == {"value": "up"}
        assert basis.condition_codes[1].params == {"value": "oversold"}
        assert basis.condition_codes[2].params == {"value": "heavy"}
        assert basis.condition_codes[3].params == {"pattern": "hammer", "direction": "bullish"}
        # The agent/MCP-facing English prose list is untouched by the additive codes.
        assert basis.conditions == [
            "trend=up",
            "momentum=oversold",
            "volume=heavy",
            "candlestick=hammer (bullish)",
        ]

    def test_signal_codes_mirror_the_signals_prose_in_id_order(self) -> None:
        basis = fuse(
            snapshot=make_snapshot(),
            signals=[
                make_signal("rsi", "long"),
                make_signal("macd", "long", fresh=False),
            ],
            walk_forward=make_walk_forward(),
            forecast=make_forecast(),
            last_close=LAST_CLOSE,
        ).basis
        # 1:1 with the prose list, sorted by strategy_id (macd before rsi); the
        # freshness rides as a 0/1 param (ReasonCode.params has no bool grain).
        assert len(basis.signal_codes) == len(basis.signals)
        assert [c.code for c in basis.signal_codes] == ["signal.vote", "signal.vote"]
        assert basis.signal_codes[0].params == {
            "strategy_id": "macd",
            "position": "long",
            "fresh": 0,
        }
        assert basis.signal_codes[1].params == {
            "strategy_id": "rsi",
            "position": "long",
            "fresh": 1,
        }
        assert basis.signals == [
            "macd: position=long",
            "rsi: position=long, fresh_signal",
        ]

    def test_condition_signal_codes_are_deterministic(self) -> None:
        def run() -> list[dict[str, object]]:
            basis = fuse(
                snapshot=make_snapshot(),
                signals=[make_signal(), make_signal("macd", "long", fresh=False)],
                walk_forward=make_walk_forward(),
                forecast=make_forecast(),
                last_close=LAST_CLOSE,
            ).basis
            return [
                code.model_dump(mode="json")
                for code in (*basis.condition_codes, *basis.signal_codes)
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
        # The technical-read tier (Plan 0074, ADR-0068) reuses the pure, trailing
        # indicator functions to compute a single-indicator regime, and reads raw
        # `Bar` inputs — an architect-sanctioned widening of the advisor's consumed
        # surface beyond the fused tier's output models.
        "market_analyser.analysis.indicators",
        "market_analyser.data.types",
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
