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

from market_analyser.advisor.models import Recommendation, RecommendationBasis

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
