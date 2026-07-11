"""Phase-2 done-when for Plan 0038 (+ Plan 0039 phase 1): the `recommend` MCP tool.

Covered:
- an aligned set of live inputs yields a directional `Recommendation`
  explicitly labeled advisory and carrying all four basis components;
- bus side-effect (Plan 0039 phase 1): exactly one `recommendation.completed
  v1` envelope per success, carrying the full `Recommendation` inline —
  advisory label and basis included; nothing published on any failure;
- conviction maps from the forecast probability + backtested edge — varying
  either moves it (not a constant);
- a no-edge forecast (and any other failed leg) yields the honest flat
  "no actionable edge" verdict, never a fabricated call;
- boundary validation (symbol / timeframe / strategy / params / knobs / bars);
- **no trade-permissioned secret, no order, no network write path** exists in
  the advisor package or this tool (a source-level assertion, per ADR-0029);
- registration lives in `tests/api/test_mcp_tools.py` alongside the other
  toolset assertions.

The directional/flat branches are exercised by stubbing the three expensive
legs (live signal, walk-forward, forecast) at the module seams — their own
correctness is pinned by `tests/backtest/` and `tests/forecast/`; the
condition snapshot runs for real over synthetic bars. The fusion logic itself
is pinned by `tests/advisor/test_fusion.py`.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

import market_analyser.advisor
from market_analyser.api.mcp_tools import recommend as recommend_tool
from market_analyser.api.mcp_tools.forecast import (
    FALLBACK_REASON_UNWIRED,
    _compute_multi_horizon_forecast,
)
from market_analyser.api.mcp_tools.recommend import (
    RECOMMEND_DESCRIPTION,
    RecommendationExplanationArtifact,
    _as_forecast_result,
    _assemble_and_fuse,
    _recommend_response,
)
from market_analyser.backtest.result import BacktestMetrics
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.contracts.strategy import discover
from market_analyser.data.types import Bar
from market_analyser.events import Envelope, EventBus
from market_analyser.forecast.features import FEATURE_SET_ID
from market_analyser.forecast.regime import RegimeForecast, RegimeState, RegimeValidation
from market_analyser.forecast.result import (
    EdgeStrength,
    ForecastProvenance,
    ForecastResult,
    HorizonForecast,
    MultiHorizonForecastResult,
)
from market_analyser.forecast.validation import ForecastValidation
from market_analyser.forecast.volatility import VolatilityForecast, VolatilityValidation
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from tests.forecast._synthetic import synthetic_bars

BARS = synthetic_bars(220)
# The synthetic series is daily from 2025-01-01; a NOW far past the last bar
# keeps every bar closed, so the closed-bar filter is the identity here.
NOW = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_START = datetime(2025, 1, 1, tzinfo=UTC)
LAST_TS = BARS[-1].event_ts


class _StubProvider:
    """Returns a fixed bar list on get_ohlcv; everything else is unused."""

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[Bar]:
        return self._bars


def _signal_evaluation(
    position: Literal["flat", "long", "short"],
) -> SignalEvaluation:
    return SignalEvaluation(
        strategy_id="rsi",
        symbol="SYN",
        timeframe="1d",
        evaluated_through_ts=LAST_TS,
        closed_bar_count=len(BARS),
        latest_bar_excluded_as_forming=False,
        current_position=position,
        fresh_signal=position != "flat",
    )


def _walk_forward_result(sharpe_mean: float) -> WalkForwardResult:
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
        symbol="SYN",
        timeframe="1d",
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


def _forecast_result(
    *,
    prob_up: float | None,
    prob_down: float | None,
    prob_flat: float | None,
    beats_baseline: bool,
    edge_strength: EdgeStrength,
    feature_set_id: str = "fs-v1",
    fallback_reason: str | None = None,
) -> ForecastResult:
    return ForecastResult(
        symbol="SYN",
        timeframe="1d",
        as_of_bar_ts=LAST_TS,
        horizon_bars=1,
        prob_up=prob_up,
        prob_down=prob_down,
        prob_flat=prob_flat,
        validation=ForecastValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=120,
            skill=0.58 if beats_baseline else 0.35,
            baseline_skill=0.50,
            persistence_skill=0.50,
            majority_skill=0.44,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=ForecastProvenance(
            model_version="deadbeef",
            feature_set_id=feature_set_id,
            training_cutoff=LAST_TS,
            seed=1729,
            lib_versions={"scikit-learn": "1.8.0"},
            fallback_reason=fallback_reason,
        ),
        edge_margin=0.08 if beats_baseline else -0.15,
        edge_strength=edge_strength,
    )


def _volatility_forecast(
    *, predicted_vol: float | None = 0.05, beats_baseline: bool = True
) -> VolatilityForecast:
    return VolatilityForecast(
        symbol="SYN",
        timeframe="1d",
        as_of_bar_ts=LAST_TS,
        horizon_bars=1,
        predicted_vol=predicted_vol,
        band=(predicted_vol * 0.8, predicted_vol * 1.2) if predicted_vol is not None else None,
        baseline_vol=0.03,
        baseline_kind="ewma",
        beats_baseline=beats_baseline,
        score_margin=0.1 if beats_baseline else -0.1,
        validation=VolatilityValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=100,
            model_qlike=1.0,
            baseline_qlike=1.1,
            baseline_kind="ewma",
            persistence_qlike=1.2,
            ewma_qlike=1.1,
            score_margin=0.1,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=None,
    )


def _regime_forecast(*, beats_baseline: bool = True) -> RegimeForecast:
    probs = {s: 0.0 for s in RegimeState}
    probs[RegimeState.UP_QUIET] = 0.7
    probs[RegimeState.UP_VOLATILE] = 0.3
    return RegimeForecast(
        symbol="SYN",
        timeframe="1d",
        as_of_bar_ts=LAST_TS,
        horizon_bars=1,
        current_regime=RegimeState.UP_QUIET,
        transition_probs=probs,
        beats_baseline=beats_baseline,
        score_margin=0.05 if beats_baseline else -0.05,
        validation=RegimeValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=100,
            model_brier=0.5,
            persistence_brier=0.6,
            score_margin=0.1,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=None,
    )


def _multi_forecast_result(**kwargs: Any) -> MultiHorizonForecastResult:
    """Wrap `_forecast_result` into the single-block `MultiHorizonForecastResult`
    the tiered core returns (Plan 0066) — so a stub at the
    `_compute_multi_horizon_forecast` seam still drives the REAL
    `_as_forecast_result` adapter the advisor now runs. Reconstructing the
    result field-for-field from `_forecast_result` guarantees
    `_as_forecast_result(_multi_forecast_result(**k)) == _forecast_result(**k)`."""

    fr = _forecast_result(**kwargs)
    assert fr.provenance is not None
    block = HorizonForecast(
        horizon_bars=fr.horizon_bars,
        prob_up=fr.prob_up,
        prob_down=fr.prob_down,
        prob_flat=fr.prob_flat,
        validation=fr.validation,
        edge_margin=fr.edge_margin,
        edge_strength=fr.edge_strength,
        provenance=fr.provenance,
    )
    return MultiHorizonForecastResult(
        symbol=fr.symbol,
        timeframe=fr.timeframe,
        as_of_bar_ts=fr.as_of_bar_ts,
        feature_set_id=fr.provenance.feature_set_id,
        horizons=[block],
    )


def _patch_legs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    position: Literal["flat", "long", "short"] = "long",
    sharpe_mean: float = 0.8,
    prob_up: float | None = 0.60,
    prob_down: float | None = 0.25,
    prob_flat: float | None = 0.15,
    beats_baseline: bool = True,
    edge_strength: EdgeStrength = "clear",
    feature_set_id: str = "fs-v1",
    fallback_reason: str | None = None,
    volatility: VolatilityForecast | None = None,
    regime: RegimeForecast | None = None,
) -> None:
    """Stub the two expensive backtest legs, the forecast core, and the two
    non-voting vol/regime legs at the module seams; the condition snapshot and the
    `_as_forecast_result` adapter stay real (the adapter is on the advisor's
    forecast path — Plan 0066 — so every fusion test exercises it). The vol/regime
    stubs default to ``None`` (neutral: no sizing/regime context), so tests that do
    not opt in behave exactly as pre-0077 (Plan 0077 phase 5)."""

    monkeypatch.setattr(
        recommend_tool,
        "evaluate_signals_core",
        lambda *a, **kw: _signal_evaluation(position),
    )
    monkeypatch.setattr(
        recommend_tool,
        "walk_forward",
        lambda *a, **kw: _walk_forward_result(sharpe_mean),
    )
    monkeypatch.setattr(
        recommend_tool,
        "_compute_multi_horizon_forecast",
        lambda **kw: _multi_forecast_result(
            prob_up=prob_up,
            prob_down=prob_down,
            prob_flat=prob_flat,
            beats_baseline=beats_baseline,
            edge_strength=edge_strength,
            feature_set_id=feature_set_id,
            fallback_reason=fallback_reason,
        ),
    )
    monkeypatch.setattr(recommend_tool, "forecast_volatility", lambda *a, **kw: volatility)
    monkeypatch.setattr(recommend_tool, "forecast_regime", lambda *a, **kw: regime)


def _run(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "provider": _StubProvider(list(BARS)),
        "coordinator": None,
        "event_bus": EventBus(),
        "models_dir": None,
        "strategy_id": "rsi",
        "symbol": "SYN",
        "timeframe": "1d",
        "range_start": RANGE_START,
        "params": None,
        "horizon_bars": 1,
        "flat_band": 0.001,
        "n_splits": 5,
        "seed": 1729,
        "now": NOW,
    }
    kwargs.update(overrides)
    return asyncio.run(_recommend_response(**kwargs))


class TestAdvisoryOutput:
    def test_aligned_inputs_return_labeled_directional_recommendation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch)
        rec = _run()
        assert rec.label == "advisory"
        assert rec.direction == "long"
        assert rec.symbol == "SYN" and rec.timeframe == "1d"
        # All four basis components travel with the call (phase-2 done-when).
        assert rec.basis.conditions  # ADR-0023 snapshot facts (computed for real)
        assert rec.basis.signals  # Plan 0026 live signal
        assert rec.basis.backtest is not None  # ADR-0024 walk-forward edge
        assert rec.basis.forecast is not None  # Plan 0036 forecast
        assert rec.rationale
        assert rec.entry_zone is not None and rec.stop is not None and rec.targets
        assert rec.as_of_bar_ts == LAST_TS  # the shared as-of bar

    def test_conviction_moves_with_forecast_probability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch, prob_up=0.52, prob_down=0.33)
        low = _run().conviction
        _patch_legs(monkeypatch, prob_up=0.72, prob_down=0.13)
        high = _run().conviction
        assert high > low  # not a constant — maps from the forecast probability

    def test_conviction_moves_with_backtested_edge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_legs(monkeypatch, sharpe_mean=0.3)
        thin = _run().conviction
        _patch_legs(monkeypatch, sharpe_mean=0.9)
        strong = _run().conviction
        assert strong > thin  # maps from the walk-forward edge


class TestHonestFlat:
    def test_no_edge_forecast_is_demoted_not_a_veto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plan 0077 phase 5 (ADR-0071): a no-edge direction forecast is demoted —
        it no longer vetoes a call the live signal + backtested edge corroborate.
        (Pre-0077 this exact set was flat.) The demotion is recorded on the
        `direction_leg` status the viewer renders."""

        _patch_legs(
            monkeypatch,
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            beats_baseline=False,
            edge_strength="no_edge",
        )
        rec = _run()
        assert rec.direction == "long"  # corroborated, not vetoed
        assert rec.label == "advisory"
        assert rec.direction_leg is not None
        assert rec.direction_leg.present is True and rec.direction_leg.gating is False
        assert rec.basis.forecast is not None
        assert rec.basis.forecast["beats_baseline"] is False
        assert any("non-gating" in line for line in rec.rationale)

    def test_no_signal_still_yields_flat_no_actionable_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no directional live signal to carry it, a no-edge forecast still
        yields the honest flat — the demotion removes the veto, it does not
        manufacture a call from nothing."""

        _patch_legs(
            monkeypatch,
            position="flat",
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            beats_baseline=False,
            edge_strength="no_edge",
        )
        rec = _run()
        assert rec.direction == "flat"
        assert rec.conviction == 0.0
        assert rec.entry_zone is None and rec.stop is None and rec.targets == []
        assert rec.rationale[0] == "no actionable edge"
        assert rec.sizing is None and rec.regime_context is None

    def test_signal_conflicting_with_forecast_yields_flat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch, position="short")  # forecast says long (gating)
        rec = _run()
        assert rec.direction == "flat"
        assert any("disagree" in line for line in rec.rationale)


class TestNonVotingLegsWired:
    """Plan 0077 phase 5: the `recommend` tool computes the vol/regime legs and
    passes them to `fuse()`, so the recommendation carries the sizing/regime
    context and the persisted explanation artifact captures both raw legs."""

    def test_vol_regime_flow_into_the_recommendation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_legs(
            monkeypatch,
            volatility=_volatility_forecast(predicted_vol=0.06, beats_baseline=True),
            regime=_regime_forecast(beats_baseline=True),
        )
        rec = _run()
        assert rec.direction == "long"
        assert rec.sizing is not None and rec.sizing.vol_source == "model"
        assert rec.sizing.size_factor < 1.0  # 0.06 > reference vol → smaller
        assert rec.regime_context is not None
        assert rec.regime_context.current_regime == "up_quiet"

    def test_leg_inputs_capture_the_non_voting_legs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_legs(
            monkeypatch,
            volatility=_volatility_forecast(),
            regime=_regime_forecast(),
        )
        _rec, legs = _assemble_and_fuse(
            strategy_module=discover()["rsi"],
            closed_bars=list(BARS),
            params_instance=discover()["rsi"].Params(),
            timeframe="1d",
            horizon_bars=1,
            flat_band=0.001,
            n_splits=5,
            seed=1729,
            models_dir=None,
            metric_lookup=None,
            now=NOW,
        )
        assert legs.volatility is not None and legs.volatility.symbol == "SYN"
        assert legs.regime is not None and legs.regime.current_regime == RegimeState.UP_QUIET


class TestForecastLegAdapter:
    """Plan 0066: `_as_forecast_result` adapts the tiered core's single block to
    the `ForecastResult` `fuse()` consumes. It preserves the pre-0066 failure
    contract (a block that could not train at all → the same `ValueError` the
    advisor's forecast leg raised before) while passing an honest no-edge block
    (provenance present, `prob_*` null) straight through — fusion reads that as a
    blocked directional call, not an error."""

    def test_maps_untrainable_block_to_valueerror(self) -> None:
        untrainable = HorizonForecast(
            horizon_bars=1,
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            validation=ForecastValidation(
                horizon_bars=1,
                n_splits=5,
                n_scored=0,
                skill=None,
                baseline_skill=None,
                persistence_skill=None,
                majority_skill=None,
                beats_baseline=False,
                folds=[],
            ),
            edge_margin=None,
            edge_strength="no_edge",
            provenance=None,  # nothing trained — no model to version
        )
        multi = MultiHorizonForecastResult(
            symbol="SYN",
            timeframe="1d",
            as_of_bar_ts=LAST_TS,
            feature_set_id=FEATURE_SET_ID,
            horizons=[untrainable],
        )
        with pytest.raises(ValueError, match="insufficient labelled history"):
            _as_forecast_result(multi)

    def test_passes_no_edge_block_through(self) -> None:
        multi = _multi_forecast_result(
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            beats_baseline=False,
            edge_strength="no_edge",
        )
        fr = _as_forecast_result(multi)
        assert fr.prob_up is None
        assert fr.validation.beats_baseline is False
        assert fr.provenance.feature_set_id == "fs-v1"
        # A faithful projection of the block back to the single-horizon shape.
        assert fr == _forecast_result(
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            beats_baseline=False,
            edge_strength="no_edge",
        )


class TestForecastUnification:
    """Plan 0066 phase-1 done-when: the advisor's forecast leg runs the SAME
    tiered core the `forecast` tool does — identical tier, probabilities, and
    skill at a shared horizon (the 0063 divergence, closed) — and still falls
    back to the stated v1 set when no metric store is wired."""

    @staticmethod
    def _leg_forecast(monkeypatch: pytest.MonkeyPatch, *, metric_lookup: Any) -> ForecastResult:
        """Run `_assemble_and_fuse` with the two backtest legs stubbed but the
        forecast leg REAL, and return the `ForecastResult` it fused from."""

        monkeypatch.setattr(
            recommend_tool,
            "evaluate_signals_core",
            lambda *a, **kw: _signal_evaluation("long"),
        )
        monkeypatch.setattr(
            recommend_tool,
            "walk_forward",
            lambda *a, **kw: _walk_forward_result(0.8),
        )
        strategy_module = discover()["rsi"]
        _, legs = _assemble_and_fuse(
            strategy_module=strategy_module,
            closed_bars=list(BARS),
            params_instance=strategy_module.Params(),
            timeframe="1d",
            horizon_bars=1,
            flat_band=0.001,
            n_splits=5,
            seed=1729,
            models_dir=None,
            metric_lookup=metric_lookup,
            now=NOW,
        )
        return legs.forecast

    def test_leg_matches_forecast_core_with_store_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = make_engine(":memory:")
        apply_migrations(engine)
        store = MetricPointsRepository(make_session_factory(engine))
        fr = self._leg_forecast(monkeypatch, metric_lookup=store)
        core = _compute_multi_horizon_forecast(
            bars=list(BARS),
            symbol="SYN",
            timeframe="1d",
            horizons=(1,),
            flat_band=0.001,
            n_splits=5,
            seed=1729,
            models_dir=None,
            metric_lookup=store,
        )
        engine.dispose()

        (block,) = core.horizons
        assert block.provenance is not None
        # Same tier, same probabilities, same skill — the divergence is closed.
        assert fr.provenance.feature_set_id == block.provenance.feature_set_id
        assert fr.prob_up == block.prob_up
        assert fr.prob_down == block.prob_down
        assert fr.prob_flat == block.prob_flat
        assert fr.validation.skill == block.validation.skill
        assert fr.validation.baseline_skill == block.validation.baseline_skill
        assert fr.validation.beats_baseline == block.validation.beats_baseline
        # The advisor's leg IS the tool's core, adapted — byte-identical.
        assert fr == _as_forecast_result(core)

    def test_leg_is_v1_with_unwired_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fr = self._leg_forecast(monkeypatch, metric_lookup=None)
        # No store → the terminal v1 tier, stated (not silent) — the leg still
        # produces a usable forecast offline, carrying the 0063 explanation.
        assert fr.provenance.feature_set_id == FEATURE_SET_ID
        assert fr.provenance.series_inputs == ()
        assert fr.provenance.fallback_reason == FALLBACK_REASON_UNWIRED
        assert fr.provenance.explanation is not None

    def test_advice_artifact_forecast_leg_round_trips_the_tier(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The persisted advice explanation's forecast leg carries the tier's
        `series_inputs` + the 0063 explanation and round-trips through the
        artifact model — exactly the tiered core's output, adapted."""

        monkeypatch.setattr(
            recommend_tool,
            "evaluate_signals_core",
            lambda *a, **kw: _signal_evaluation("long"),
        )
        monkeypatch.setattr(
            recommend_tool,
            "walk_forward",
            lambda *a, **kw: _walk_forward_result(0.8),
        )
        _run(runs_dir=tmp_path)  # real forecast leg, no store wired → v1

        stamp = NOW.strftime("%Y%m%dT%H%M%S%fZ")
        target = tmp_path / "advice" / f"{stamp}-SYN" / "explanation.json"
        assert target.is_file()
        artifact = RecommendationExplanationArtifact.model_validate_json(
            target.read_text(encoding="utf-8")
        )
        leg = artifact.inputs.forecast
        assert leg.provenance.feature_set_id == FEATURE_SET_ID
        assert leg.provenance.series_inputs == ()  # v1 tier: named, empty
        assert leg.provenance.fallback_reason == FALLBACK_REASON_UNWIRED
        assert leg.provenance.explanation is not None  # 0063 explanation rides along

        # The persisted leg IS the tiered core's output for these inputs.
        core = _compute_multi_horizon_forecast(
            bars=list(BARS),
            symbol="SYN",
            timeframe="1d",
            horizons=(1,),
            flat_band=0.001,
            n_splits=5,
            seed=1729,
            models_dir=None,
            metric_lookup=None,
        )
        assert leg == _as_forecast_result(core)

    def test_recommendation_basis_carries_the_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plan 0066 phase 2: the recommendation's `basis.forecast` carries the
        tier (`feature_set_id`) + `fallback_reason` the real leg produced, equal
        to what the forecast core reports for the same inputs (unwired → v1)."""

        monkeypatch.setattr(
            recommend_tool,
            "evaluate_signals_core",
            lambda *a, **kw: _signal_evaluation("long"),
        )
        monkeypatch.setattr(
            recommend_tool,
            "walk_forward",
            lambda *a, **kw: _walk_forward_result(0.8),
        )
        rec = _run()  # real forecast leg, no store wired → v1

        assert rec.basis.forecast is not None
        assert rec.basis.forecast["feature_set_id"] == FEATURE_SET_ID
        assert rec.basis.forecast["fallback_reason"] == FALLBACK_REASON_UNWIRED

        core = _compute_multi_horizon_forecast(
            bars=list(BARS),
            symbol="SYN",
            timeframe="1d",
            horizons=(1,),
            flat_band=0.001,
            n_splits=5,
            seed=1729,
            models_dir=None,
            metric_lookup=None,
        )
        (block,) = core.horizons
        assert block.provenance is not None
        assert rec.basis.forecast["feature_set_id"] == block.provenance.feature_set_id
        assert rec.basis.forecast["fallback_reason"] == block.provenance.fallback_reason


def _run_draining_bus(**overrides: Any) -> tuple[Any, list[Envelope]]:
    """Run `_recommend_response` with a subscription open on its bus and
    return `(recommendation, envelopes)` — the subscription is opened before
    the call so nothing published can be missed."""

    bus = EventBus()

    async def _go() -> tuple[Any, list[Envelope]]:
        sub = bus.subscribe()
        try:
            kwargs: dict[str, Any] = {
                "provider": _StubProvider(list(BARS)),
                "coordinator": None,
                "event_bus": bus,
                "models_dir": None,
                "strategy_id": "rsi",
                "symbol": "SYN",
                "timeframe": "1d",
                "range_start": RANGE_START,
                "params": None,
                "horizon_bars": 1,
                "flat_band": 0.001,
                "n_splits": 5,
                "seed": 1729,
                "now": NOW,
            }
            kwargs.update(overrides)
            rec = await _recommend_response(**kwargs)
            envelopes: list[Envelope] = []
            try:
                while True:
                    envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.3))
            except TimeoutError:
                pass
            return rec, envelopes
        finally:
            sub.close()

    return asyncio.run(_go())


class TestEventEmission:
    """Plan 0039 phase 1 done-when: calling `recommend` emits exactly one
    `recommendation.completed v1` envelope carrying the full `Recommendation`
    including its `advisory` label and basis."""

    def test_success_publishes_exactly_one_envelope_with_full_recommendation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch)
        rec, envelopes = _run_draining_bus()

        assert len(envelopes) == 1  # exactly one, not "at least one"
        envelope = envelopes[0]
        assert envelope.type == "recommendation.completed"
        assert envelope.version == 1

        payload_rec = envelope.payload["recommendation"]
        # The full Recommendation rides inline — advisory label and basis
        # included (the phase's done-when), matching the returned artifact.
        assert payload_rec["label"] == "advisory"
        assert payload_rec["direction"] == rec.direction == "long"
        assert payload_rec["symbol"] == "SYN"
        assert payload_rec["timeframe"] == "1d"
        assert payload_rec["conviction"] == rec.conviction
        assert payload_rec["rationale"] == rec.rationale
        basis = payload_rec["basis"]
        assert basis["conditions"] == rec.basis.conditions
        assert basis["signals"] == rec.basis.signals
        assert basis["backtest"] is not None
        assert basis["forecast"] is not None
        assert payload_rec["entry_zone"] == list(rec.entry_zone or ())
        assert payload_rec["stop"] == rec.stop
        assert payload_rec["targets"] == rec.targets

    def test_flat_recommendation_also_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No directional live signal → a genuine flat (Plan 0077: the demoted
        # no-edge forecast alone no longer forces flat, so drive the flat via an
        # abstaining signal instead).
        _patch_legs(
            monkeypatch,
            position="flat",
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            beats_baseline=False,
            edge_strength="no_edge",
        )
        rec, envelopes = _run_draining_bus()
        assert rec.direction == "flat"
        assert len(envelopes) == 1
        payload_rec = envelopes[0].payload["recommendation"]
        assert payload_rec["direction"] == "flat"
        assert payload_rec["label"] == "advisory"

    @staticmethod
    def _failing_run_envelopes(match: str, **overrides: Any) -> list[Envelope]:
        """Expect `_recommend_response` to raise; return whatever hit the bus."""
        bus = EventBus()

        async def _go() -> list[Envelope]:
            sub = bus.subscribe()
            try:
                kwargs: dict[str, Any] = {
                    "provider": _StubProvider(list(BARS)),
                    "coordinator": None,
                    "event_bus": bus,
                    "models_dir": None,
                    "strategy_id": "rsi",
                    "symbol": "SYN",
                    "timeframe": "1d",
                    "range_start": RANGE_START,
                    "params": None,
                    "horizon_bars": 1,
                    "flat_band": 0.001,
                    "n_splits": 5,
                    "seed": 1729,
                    "now": NOW,
                }
                kwargs.update(overrides)
                with pytest.raises(ValueError, match=match):
                    await _recommend_response(**kwargs)
                envelopes: list[Envelope] = []
                try:
                    while True:
                        envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.2))
                except TimeoutError:
                    pass
                return envelopes
            finally:
                sub.close()

        return asyncio.run(_go())

    def test_validation_failure_publishes_nothing(self) -> None:
        assert self._failing_run_envelopes("unknown strategy_id", strategy_id="nope") == []

    def test_no_bars_failure_publishes_nothing(self) -> None:
        """A failure past validation (empty bar fetch) also leaves the bus
        untouched — the publish sits strictly after the fusion."""
        assert (
            self._failing_run_envelopes("backfill via get_ohlcv", provider=_StubProvider([])) == []
        )


class TestExplanationArtifact:
    """Plan 0063 phase 2 done-when (tool half): the advice explanation JSON —
    fused verdict (with its full trace) + per-leg inputs — persists under
    `runs_dir/advice/…`, round-trips, and is re-run-stable modulo the
    documented run-provenance exceptions; without a `runs_dir` nothing is
    written and the trace still rides the wire."""

    def test_wired_runs_dir_writes_advice_explanation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_legs(monkeypatch)
        rec = _run(runs_dir=tmp_path)

        stamp = NOW.strftime("%Y%m%dT%H%M%S%fZ")
        target = tmp_path / "advice" / f"{stamp}-SYN" / "explanation.json"
        assert target.is_file()
        artifact = RecommendationExplanationArtifact.model_validate_json(
            target.read_text(encoding="utf-8")
        )
        assert artifact.symbol == "SYN"
        assert artifact.timeframe == "1d"
        assert artifact.strategy_id == "rsi"
        assert artifact.started_at == NOW
        # The fused verdict persists whole — trace included — and matches the
        # returned recommendation exactly.
        assert artifact.recommendation == rec
        assert artifact.recommendation.basis.checks
        # The per-leg inputs are exactly what fuse() consumed.
        assert artifact.inputs.forecast == _forecast_result(
            prob_up=0.60,
            prob_down=0.25,
            prob_flat=0.15,
            beats_baseline=True,
            edge_strength="clear",
        )
        assert artifact.inputs.walk_forward == _walk_forward_result(0.8)
        assert artifact.inputs.signals == (_signal_evaluation("long"),)
        assert artifact.inputs.last_close == BARS[-1].close
        assert artifact.inputs.snapshot.symbol == "SYN"

    def test_rerun_artifact_identical_modulo_started_at_and_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_legs(monkeypatch)
        _run(runs_dir=tmp_path)
        _run(runs_dir=tmp_path, now=NOW + timedelta(seconds=1))

        files = sorted(tmp_path.glob("advice/*/explanation.json"))
        assert len(files) == 2
        first, second = (
            RecommendationExplanationArtifact.model_validate_json(file.read_text(encoding="utf-8"))
            for file in files
        )
        assert first.started_at != second.started_at
        assert first.model_dump(exclude={"started_at"}) == second.model_dump(exclude={"started_at"})

    def test_unwired_runs_dir_writes_nothing_and_trace_rides_the_wire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch)
        rec, envelopes = _run_draining_bus()  # no runs_dir wired

        assert rec.basis.checks  # the trace travels regardless
        payload_checks = envelopes[0].payload["recommendation"]["basis"]["checks"]
        assert payload_checks
        assert [check["check"] for check in payload_checks] == [
            check.check for check in rec.basis.checks
        ]
        # A recorded fact's None threshold is an absent key on the wire
        # (exclude_none), never a null — the renderer Zod's `.nullish()` shape.
        vote = next(check for check in payload_checks if check["check"] == "live vote: rsi")
        assert "threshold" not in vote
        assert vote["actual"] == "long"
        assert vote["passed"] is True


class TestBoundaryValidation:
    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown strategy_id"):
            _run(strategy_id="nope")

    def test_timeframe_unsupported_by_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="not supported by strategy"):
            _run(timeframe="15m")  # rsi supports 1h/1d

    def test_empty_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="symbol"):
            _run(symbol="")

    def test_unknown_param_key_rejected_at_boundary(self) -> None:
        with pytest.raises(Exception, match=r"extra_forbidden|unexpected"):
            _run(params={"not_a_param": 1})

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("horizon_bars", 0, "horizon_bars"),
            ("n_splits", 1, "n_splits"),
            ("flat_band", -0.1, "flat_band"),
        ],
    )
    def test_bad_knobs_raise(self, field: str, value: float, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            _run(**{field: value})

    def test_no_bars_raises_with_backfill_hint(self) -> None:
        with pytest.raises(ValueError, match="backfill via get_ohlcv"):
            _run(provider=_StubProvider([]))

    def test_all_forming_bars_raise(self) -> None:
        with pytest.raises(ValueError, match="no closed bars"):
            _run(now=BARS[0].event_ts + timedelta(hours=1))


def test_description_labels_the_tool_advisory() -> None:
    assert "ADVISORY" in RECOMMEND_DESCRIPTION
    assert "no order" in RECOMMEND_DESCRIPTION


def test_advisor_holds_no_key_and_no_order_path() -> None:
    """Phase-2 done-when (ADR-0029 / ADR-0025 boundary): no trade-permissioned
    secret, no order placement, and no network write path exist anywhere in
    the advisor package or the `recommend` tool. Source-level, so a future
    'just submit it' accretion fails here before it ships."""

    package_file = market_analyser.advisor.__file__
    assert package_file is not None
    sources = sorted(Path(package_file).parent.glob("*.py"))
    sources.append(Path(recommend_tool.__file__))

    forbidden_tokens = (
        "place_order",
        "create_order",
        "new_order",
        "submit_order",
        "x-mbx-apikey",
        "hmac",
        "api_key",
        "apikey",
        "trade_key",
        "private_key",
    )
    forbidden_imports = (
        "httpx",
        "requests",
        "urllib",
        "market_analyser.data._http",
        "market_analyser.persistence.secrets",
    )

    for source in sources:
        text = source.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in forbidden_tokens:
            assert token not in lowered, f"{source.name} contains forbidden token {token!r}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                for banned in forbidden_imports:
                    assert not name.startswith(banned), (
                        f"{source.name} imports {name!r} — the advisor surface "
                        "must not reach the network or any secret store"
                    )
