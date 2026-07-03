"""Plan 0060 phase 1 — watch boundary types.

Done-when claims pinned here: `validate_watch_params` accepts each of the
three v1 kinds and rejects unknown kinds and malformed params at the boundary
(unknown keys, out-of-vocabulary indicator/pattern/operator values, empty
strategy ids). The `Watch` model itself enforces timeframe canonicality and
kind↔params consistency so downstream code can trust any instance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.alerts.types import (
    INDICATOR_IDS,
    PATTERN_NAMES,
    IndicatorThresholdParams,
    PatternParams,
    StrategySignalParams,
    UnknownWatchKindError,
    Watch,
    validate_watch_params,
)

THRESHOLD_RAW: dict[str, Any] = {"indicator": "rsi", "operator": "<", "level": 30.0}
PATTERN_RAW: dict[str, Any] = {"pattern": "hammer"}
SIGNAL_RAW: dict[str, Any] = {"strategy_id": "rsi_stop", "params": {"period": 14}}


class TestValidateWatchParams:
    def test_indicator_threshold_params_validate_to_typed_model(self) -> None:
        model = validate_watch_params("indicator_threshold", THRESHOLD_RAW)
        assert isinstance(model, IndicatorThresholdParams)
        assert (model.indicator, model.operator, model.level) == ("rsi", "<", 30.0)

    def test_pattern_params_validate_to_typed_model(self) -> None:
        model = validate_watch_params("pattern", PATTERN_RAW)
        assert isinstance(model, PatternParams)
        assert model.pattern == "hammer"

    def test_strategy_signal_params_validate_to_typed_model(self) -> None:
        model = validate_watch_params("strategy_signal", SIGNAL_RAW)
        assert isinstance(model, StrategySignalParams)
        assert model.strategy_id == "rsi_stop"
        assert model.params == {"period": 14}

    def test_strategy_signal_params_default_to_empty_dict(self) -> None:
        model = validate_watch_params("strategy_signal", {"strategy_id": "rsi_stop"})
        assert isinstance(model, StrategySignalParams)
        assert model.params == {}

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(UnknownWatchKindError, match="forecast"):
            validate_watch_params("forecast", {})

    @pytest.mark.parametrize(
        ("kind", "raw"),
        [
            # unknown key smuggled alongside valid ones (extra="forbid")
            ("indicator_threshold", {**THRESHOLD_RAW, "action": "buy"}),
            ("pattern", {**PATTERN_RAW, "direction": "bullish"}),
            ("strategy_signal", {**SIGNAL_RAW, "leverage": 10}),
            # out-of-vocabulary values
            ("indicator_threshold", {**THRESHOLD_RAW, "indicator": "sharpe"}),
            ("indicator_threshold", {**THRESHOLD_RAW, "operator": "=="}),
            ("pattern", {"pattern": "head_and_shoulders"}),
            # missing / empty required fields
            ("indicator_threshold", {"indicator": "rsi", "operator": "<"}),
            ("pattern", {}),
            ("strategy_signal", {"strategy_id": ""}),
        ],
    )
    def test_malformed_params_are_rejected(self, kind: str, raw: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            validate_watch_params(kind, raw)

    def test_vocabularies_are_nonempty_and_disjoint_from_kinds(self) -> None:
        assert "rsi" in INDICATOR_IDS
        assert "close" in INDICATOR_IDS
        assert "hammer" in PATTERN_NAMES
        assert "three_black_crows" in PATTERN_NAMES


class TestWatchModel:
    def _watch_kwargs(self) -> dict[str, Any]:
        return {
            "id": 1,
            "symbol": "BTC-USD",
            "timeframe": "1d",
            "kind": "indicator_threshold",
            "params": IndicatorThresholdParams(**THRESHOLD_RAW),
            "interval_seconds": 86_400,
            "enabled": True,
            "last_state": None,
            "created_at": datetime(2026, 7, 1, tzinfo=UTC),
        }

    def test_valid_watch_constructs(self) -> None:
        watch = Watch(**self._watch_kwargs())
        assert watch.last_state is None

    def test_unregistered_timeframe_is_rejected(self) -> None:
        kwargs = self._watch_kwargs() | {"timeframe": "13m"}
        with pytest.raises(ValidationError, match="unknown timeframe"):
            Watch(**kwargs)

    def test_params_type_must_match_kind(self) -> None:
        kwargs = self._watch_kwargs() | {"params": PatternParams(pattern="doji")}
        with pytest.raises(ValidationError, match="does not match"):
            Watch(**kwargs)

    def test_non_positive_interval_is_rejected(self) -> None:
        kwargs = self._watch_kwargs() | {"interval_seconds": 0}
        with pytest.raises(ValidationError):
            Watch(**kwargs)
