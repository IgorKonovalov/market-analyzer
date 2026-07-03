"""Watch boundary types (Plan 0060 phase 1, ADR-0055).

A watch names a symbol, a canonical timeframe, an evaluation `kind`, and a
kind-discriminated `params` payload. The three v1 kinds each wrap an existing
read-only primitive:

- ``indicator_threshold`` — an indicator id from the ADR-0023 snapshot surface
  compared against a level (evaluated on the latest *closed* bar, phase 2);
- ``pattern`` — a candlestick pattern name printing on the latest closed bar;
- ``strategy_signal`` — a strategy emitting a fresh signal per
  `backtest/live_signal.py`.

`validate_watch_params` is the one boundary: unknown kinds raise
`UnknownWatchKindError`, malformed params raise pydantic `ValidationError`.
Everything downstream (repository, evaluators, scheduler) trusts a validated
`Watch`. Strategy existence and per-strategy `params` validity are a runtime
registry question, checked where the registry lives (the `create_watch` tool
and the evaluator), not here — this module stays import-light and pure.

Alert payloads are deliberately **not** defined here: `alert.triggered v1` is
an SSE payload and lives with the rest of the wire vocabulary in `events/`
(phase 3). Condition facts only, never a directive (ADR-0029 boundary).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_analyser.data.timeframes import registry_timeframes

WatchKind = Literal["indicator_threshold", "pattern", "strategy_signal"]

# The scalar indicator ids of the ADR-0023 condition-snapshot surface
# (`analysis/snapshot.py`'s `indicators` dict), plus `close`. The trailing
# percentile ranks (`rsi_pct90`/`atr_pct90`) are excluded: they are derived
# ranks private to the snapshot composition, not primitive indicator reads.
IndicatorId = Literal[
    "close",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_pct_b",
    "atr",
    "adx",
    "plus_di",
    "minus_di",
    "supertrend",
    "supertrend_direction",
    "volume",
    "vol_sma20",
    "rel_volume",
    "vol_pct90",
    "obv",
    "obv_slope",
    "vwap",
]

ThresholdOperator = Literal["<", "<=", ">", ">="]

# The candlestick pattern vocabulary — exactly the names `analysis/patterns.py`
# detectors emit. A watch naming anything else can never fire, so the boundary
# rejects it.
PatternName = Literal[
    "doji",
    "hammer",
    "hanging_man",
    "marubozu",
    "bullish_engulfing",
    "bearish_engulfing",
    "dark_cloud_cover",
    "piercing_line",
    "bullish_harami",
    "bearish_harami",
    "morning_star",
    "evening_star",
    "three_white_soldiers",
    "three_black_crows",
]

INDICATOR_IDS: frozenset[str] = frozenset(get_args(IndicatorId))
PATTERN_NAMES: frozenset[str] = frozenset(get_args(PatternName))
WATCH_KINDS: frozenset[str] = frozenset(get_args(WatchKind))


class IndicatorThresholdParams(BaseModel):
    """``indicator_threshold`` params: fire when `indicator <op> level` on the
    latest closed bar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    indicator: IndicatorId
    operator: ThresholdOperator
    level: float


class PatternParams(BaseModel):
    """``pattern`` params: fire when `pattern` completes on the latest closed bar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: PatternName


class StrategySignalParams(BaseModel):
    """``strategy_signal`` params: fire when the strategy reports `fresh_signal`.

    `params` is the strategy's own Params payload, passed through to
    `generate_signals`; its per-strategy shape is validated where the strategy
    registry lives (the `create_watch` tool and the evaluator resolve the
    module and instantiate `strategy.Params(**params)`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


WatchParams = IndicatorThresholdParams | PatternParams | StrategySignalParams

_PARAMS_MODEL_BY_KIND: dict[str, type[WatchParams]] = {
    "indicator_threshold": IndicatorThresholdParams,
    "pattern": PatternParams,
    "strategy_signal": StrategySignalParams,
}


class UnknownWatchKindError(ValueError):
    """`validate_watch_params` was called with a kind outside the closed set."""


def validate_watch_params(kind: str, params: Mapping[str, Any]) -> WatchParams:
    """Validate `(kind, params)` at the boundary and return the typed model.

    Raises `UnknownWatchKindError` for a kind outside `WATCH_KINDS` and
    pydantic `ValidationError` for params that fail the kind's model
    (unknown keys are rejected — every model is `extra="forbid"`).
    """
    model = _PARAMS_MODEL_BY_KIND.get(kind)
    if model is None:
        raise UnknownWatchKindError(
            f"unknown watch kind: {kind!r} (supported: {sorted(WATCH_KINDS)})",
        )
    return model.model_validate(dict(params))


class Watch(BaseModel):
    """A validated watch definition — the domain shape the repository returns
    and the evaluators/scheduler consume.

    `timeframe` must be a canonical registry value; `params` must be the typed
    model matching `kind` (both enforced below, so a `Watch` instance is
    trustable downstream). `last_state` is the edge-detector's persisted
    memory: the previous evaluation's predicate value, `None` until the first
    evaluation lands (a fresh watch is *armed*, not pre-fired).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    symbol: str = Field(min_length=1)
    timeframe: str
    kind: WatchKind
    params: WatchParams
    interval_seconds: Annotated[int, Field(gt=0)]
    enabled: bool
    last_state: bool | None
    created_at: datetime

    @model_validator(mode="after")
    def _validate_consistency(self) -> Watch:
        """`timeframe` in the canonical registry; `params` type matching `kind`."""
        if self.timeframe not in registry_timeframes():
            raise ValueError(
                f"unknown timeframe {self.timeframe!r} "
                f"(supported: {sorted(registry_timeframes())})",
            )
        expected = _PARAMS_MODEL_BY_KIND[self.kind]
        if not isinstance(self.params, expected):
            raise ValueError(
                f"params type {type(self.params).__name__} does not match "
                f"kind {self.kind!r} (expected {expected.__name__})",
            )
        return self


class Alert(BaseModel):
    """A fired alert read back from history.

    `payload` is the stored `alert.triggered v1` condition-only JSON — kept
    generic here (the typed payload model lives with the wire vocabulary in
    `events/`, phase 3), so history reads don't couple to payload versioning.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    watch_id: int
    fired_at: datetime
    payload: dict[str, Any]


__all__ = [
    "INDICATOR_IDS",
    "PATTERN_NAMES",
    "WATCH_KINDS",
    "Alert",
    "IndicatorId",
    "IndicatorThresholdParams",
    "PatternName",
    "PatternParams",
    "StrategySignalParams",
    "ThresholdOperator",
    "UnknownWatchKindError",
    "Watch",
    "WatchKind",
    "WatchParams",
    "validate_watch_params",
]
