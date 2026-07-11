"""`Recommendation` / `RecommendationBasis` shape tests (Plan 0038 phase 1).

The ADR-0029 containment rules are structural: a basis-free recommendation, a
directional call without rationale/backtest/forecast/levels, or a flat call
that looks like a trade ticket must all fail *at construction*.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.advisor.models import (
    DirectionLegStatus,
    FusionCheck,
    Recommendation,
    RecommendationBasis,
    RegimeContext,
    VolatilitySizing,
)

AS_OF = datetime(2026, 6, 1, tzinfo=UTC)


def _full_basis() -> RecommendationBasis:
    return RecommendationBasis(
        conditions=["trend=up"],
        signals=["rsi: position=long, fresh_signal"],
        backtest={"strategy_id": "rsi", "sharpe_mean": 0.8, "n_splits": 5},
        forecast={"prob_up": 0.6, "beats_baseline": True, "model_version": "abc123"},
    )


def _directional_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "symbol": "BTC-USD",
        "timeframe": "1d",
        "direction": "long",
        "entry_zone": (99.5, 100.5),
        "stop": 94.8,
        "targets": [108.0],
        "conviction": 0.48,
        "rationale": ["forecast: P(long)=0.600", "live signals agree (long): rsi"],
        "basis": _full_basis(),
        "label": "advisory",
        "as_of_bar_ts": AS_OF,
    }
    kwargs.update(overrides)
    return kwargs


def _flat_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs = _directional_kwargs(
        direction="flat",
        entry_zone=None,
        stop=None,
        targets=[],
        conviction=0.0,
        rationale=["no actionable edge"],
    )
    kwargs.update(overrides)
    return kwargs


class TestRecommendationBasis:
    def test_empty_basis_raises(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            RecommendationBasis(conditions=[], signals=[], backtest=None, forecast=None)

    def test_any_single_component_suffices(self) -> None:
        basis = RecommendationBasis(
            conditions=["trend=up"], signals=[], backtest=None, forecast=None
        )
        assert basis.conditions == ["trend=up"]

    def test_checks_default_empty_and_do_not_satisfy_non_emptiness(self) -> None:
        """Plan 0063: `checks` is additive (defaulted) and a trace alone is not
        a basis — the ADR-0029 non-emptiness rule still needs a real leg."""

        assert _full_basis().checks == ()
        with pytest.raises(ValidationError, match="must not be empty"):
            RecommendationBasis(
                conditions=[],
                signals=[],
                backtest=None,
                forecast=None,
                checks=(
                    FusionCheck(
                        leg="forecast",
                        check="probabilities shipped (baseline beaten out-of-sample)",
                        threshold=True,
                        actual=False,
                        passed=False,
                    ),
                ),
            )


class TestFusionCheck:
    def test_constructs_with_real_threshold_and_actual(self) -> None:
        check = FusionCheck(
            leg="backtest",
            check="backtested edge positive (sharpe_mean > 0)",
            threshold=0.0,
            actual=0.8,
            passed=True,
        )
        assert check.threshold == 0.0
        assert check.actual == 0.8

    def test_leg_admits_only_the_five_fusion_legs(self) -> None:
        bad_leg: dict[str, Any] = {
            "leg": "orders",
            "check": "x",
            "threshold": None,
            "actual": None,
            "passed": True,
        }
        with pytest.raises(ValidationError):
            FusionCheck(**bad_leg)

    def test_frozen_and_extra_forbidden(self) -> None:
        check = FusionCheck(
            leg="signal", check="live vote: rsi", threshold=None, actual="long", passed=True
        )
        with pytest.raises(ValidationError):
            check.passed = False
        extra_field: dict[str, Any] = {
            "leg": "signal",
            "check": "x",
            "threshold": None,
            "actual": None,
            "passed": True,
            "extra": "y",
        }
        with pytest.raises(ValidationError):
            FusionCheck(**extra_field)

    def test_wire_dump_strips_none_threshold_and_actual(self) -> None:
        """`exclude_none` semantics the renderer Zod relies on: a recorded
        fact's None threshold/actual are absent keys, never nulls. `gating`
        (Plan 0077 phase 5) rides on the wire — it defaults True and is a bool,
        so it is never stripped."""

        check = FusionCheck(
            leg="signal", check="live vote: rsi", threshold=None, actual="long", passed=True
        )
        wire = check.model_dump(mode="json", exclude_none=True)
        assert wire == {
            "leg": "signal",
            "check": "live vote: rsi",
            "actual": "long",
            "passed": True,
            "gating": True,
        }

    def test_gating_defaults_true_and_records_a_non_blocking_check(self) -> None:
        """Plan 0077 phase 5 (ADR-0071): `gating` defaults True (pre-0077 checks
        all gated); a `gating=False` check is recorded but does not block."""

        assert (
            FusionCheck(leg="signal", check="x", threshold=None, actual=None, passed=True).gating
            is True
        )
        demoted = FusionCheck(
            leg="forecast",
            check="argmax direction is directional",
            threshold="long or short",
            actual="none",
            passed=False,
            gating=False,
        )
        assert demoted.gating is False and demoted.passed is False


class TestRecommendation:
    def test_valid_directional_call_constructs(self) -> None:
        rec = Recommendation(**_directional_kwargs())
        assert rec.direction == "long"
        assert rec.label == "advisory"

    def test_valid_flat_call_constructs(self) -> None:
        rec = Recommendation(**_flat_kwargs())
        assert rec.direction == "flat"
        assert rec.conviction == 0.0

    def test_basis_is_required(self) -> None:
        kwargs = _directional_kwargs()
        del kwargs["basis"]
        with pytest.raises(ValidationError):
            Recommendation(**kwargs)

    def test_directional_without_backtest_basis_raises(self) -> None:
        basis = RecommendationBasis(
            conditions=["trend=up"],
            signals=["rsi: position=long"],
            backtest=None,
            forecast={"prob_up": 0.6},
        )
        with pytest.raises(ValidationError, match="backtested basis"):
            Recommendation(**_directional_kwargs(basis=basis))

    def test_directional_without_forecast_basis_raises(self) -> None:
        basis = RecommendationBasis(
            conditions=["trend=up"],
            signals=["rsi: position=long"],
            backtest={"sharpe_mean": 0.8},
            forecast=None,
        )
        with pytest.raises(ValidationError, match="forecast basis"):
            Recommendation(**_directional_kwargs(basis=basis))

    def test_directional_with_empty_rationale_raises(self) -> None:
        with pytest.raises(ValidationError, match="rationale"):
            Recommendation(**_directional_kwargs(rationale=[]))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"entry_zone": None},
            {"stop": None},
            {"targets": []},
        ],
    )
    def test_directional_without_levels_raises(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="entry zone, a stop"):
            Recommendation(**_directional_kwargs(**overrides))

    def test_inverted_entry_zone_raises(self) -> None:
        with pytest.raises(ValidationError, match="low <= high"):
            Recommendation(**_directional_kwargs(entry_zone=(100.5, 99.5)))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"entry_zone": (99.5, 100.5)},
            {"stop": 94.8},
            {"targets": [108.0]},
        ],
    )
    def test_flat_with_levels_raises(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="no entry/stop/target"):
            Recommendation(**_flat_kwargs(**overrides))

    def test_flat_with_nonzero_conviction_raises(self) -> None:
        with pytest.raises(ValidationError, match="zero conviction"):
            Recommendation(**_flat_kwargs(conviction=0.2))

    def test_label_admits_only_advisory(self) -> None:
        with pytest.raises(ValidationError):
            Recommendation(**_directional_kwargs(label="order"))

    def test_conviction_bounded_to_unit_interval(self) -> None:
        with pytest.raises(ValidationError):
            Recommendation(**_directional_kwargs(conviction=1.2))

    def test_frozen_and_extra_forbidden(self) -> None:
        rec = Recommendation(**_directional_kwargs())
        with pytest.raises(ValidationError):
            rec.direction = "short"
        with pytest.raises(ValidationError):
            Recommendation(**_directional_kwargs(order_id="x"))


class TestNonVotingInputs:
    """Plan 0077 phase 5 (ADR-0071): the non-voting vol/regime inputs and the
    demoted direction leg's status ride as appended, defaulted fields; a flat
    call carries no sizing/regime context (nothing to shape)."""

    def test_appended_fields_default_absent(self) -> None:
        """Defaulted so pre-0077 constructors stay valid — a call built without
        them is a well-formed recommendation with the fields absent."""

        rec = Recommendation(**_directional_kwargs())
        assert rec.sizing is None
        assert rec.regime_context is None
        assert rec.direction_leg is None

    def test_directional_carries_the_non_voting_blocks(self) -> None:
        rec = Recommendation(
            **_directional_kwargs(
                sizing=VolatilitySizing(
                    size_factor=0.75, vol_used=0.04, vol_source="model", stop_vol_distance=8.0
                ),
                regime_context=RegimeContext(
                    current_regime="up_quiet", trusted=True, conviction_factor=0.8
                ),
                direction_leg=DirectionLegStatus(present=True, gating=True, skill_margin=0.05),
            )
        )
        assert rec.sizing is not None and rec.sizing.size_factor == 0.75
        assert rec.regime_context is not None and rec.regime_context.current_regime == "up_quiet"
        assert rec.direction_leg is not None and rec.direction_leg.gating is True

    def test_flat_may_carry_direction_leg_status(self) -> None:
        """A flat verdict records the (possibly demoted) direction leg — the
        gating decision is auditable even when nothing was called."""

        rec = Recommendation(
            **_flat_kwargs(
                direction_leg=DirectionLegStatus(present=True, gating=False, skill_margin=None)
            )
        )
        assert rec.direction_leg is not None and rec.direction_leg.gating is False

    @pytest.mark.parametrize(
        "overrides",
        [
            {
                "sizing": VolatilitySizing(
                    size_factor=1.0, vol_used=None, vol_source="none", stop_vol_distance=None
                )
            },
            {
                "regime_context": RegimeContext(
                    current_regime=None, trusted=False, conviction_factor=1.0
                )
            },
        ],
    )
    def test_flat_with_sizing_or_regime_context_raises(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="no size or conviction"):
            Recommendation(**_flat_kwargs(**overrides))
