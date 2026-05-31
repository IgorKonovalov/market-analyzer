"""Shared analysis types (Plan 0018).

`PatternHit` (phase 2) and `ConditionSnapshot` / `Trend` / `MomentumStance`
(phase 3) live here — the plan's data-shapes section places the shared types in
`analysis/types.py`. All models are frozen with `extra="forbid"`: boundary-
validated, trustable downstream.

`ConditionSnapshot` reports *conditions only* — it has no buy/sell/action field,
the analyst non-negotiable enforced at the type level (and guarded by a test that
pins the exact field set).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

Direction = Literal["bullish", "bearish", "neutral"]


class PatternHit(BaseModel):
    """A candlestick pattern detected at a specific bar.

    `bar_index` is the index of the *latest* bar of the formation in the input
    series (the bar at which the pattern completes). `strength` is a detector-
    defined score in `[0, 1]` — relative conviction, not a probability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bar_index: int
    pattern: str
    direction: Direction
    strength: float


class Trend(StrEnum):
    """Coarse trend classification from the EMA stack + ADX strength."""

    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


class MomentumStance(StrEnum):
    """Momentum reading from the RSI zone refined by MACD sign."""

    OVERBOUGHT = "overbought"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    OVERSOLD = "oversold"


class VolumeStance(StrEnum):
    """Coarse volume reading from relative volume (latest ÷ trailing MA).

    Conditions only — heavy/normal/light describes *how much* trading is
    happening relative to the trailing average, never a buy/sell call.
    """

    HEAVY = "heavy"  # latest volume >= HEAVY_MULT * trailing MA
    NORMAL = "normal"
    LIGHT = "light"  # latest volume <= LIGHT_MULT * trailing MA


class VolumeSummary(BaseModel):
    """Latest trailing volume measures composed for one symbol (Plan 0027).

    Every numeric field is the latest *defined* value of its trailing series, or
    ``None`` when the available bars are too few for that measure. `stance` is the
    coarse `VolumeStance` derived from `relative_volume`; it falls back to
    ``NORMAL`` when relative volume is undefined. The VWAP is a rolling trailing
    N-period volume-weighted average of the typical price — **not** session VWAP
    (our bars are predominantly daily with no intraday session boundaries).
    Conditions only — no buy/sell field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    latest_volume: float | None
    volume_sma: float | None
    relative_volume: float | None  # latest ÷ trailing MA
    volume_percentile: float | None  # 0..100 trailing rank of the latest volume
    obv: float | None
    obv_slope: float | None  # signed; >0 accumulation, <0 distribution
    vwap: float | None  # rolling trailing N-period
    stance: VolumeStance


class ConditionSnapshot(BaseModel):
    """A composed, point-in-time technical condition read over cached bars.

    Conditions only — no buy/sell/action field, by the analyst non-negotiable.
    `indicators` carries the latest values keyed by name (e.g. ``rsi``, ``macd``,
    ``bb_pct_b``, ``atr``, ``adx``, ``supertrend_direction``, plus the trailing
    percentile ranks ``rsi_pct90`` / ``atr_pct90``); a value is ``None`` when the
    indicator is undefined over the available bars. `support_resistance` maps
    ``"support"`` / ``"resistance"`` to trailing swing levels.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of: datetime
    trend: Trend
    momentum: MomentumStance
    indicators: dict[str, float | None]
    support_resistance: dict[str, list[float]]
    recent_patterns: list[PatternHit]


__all__ = [
    "ConditionSnapshot",
    "Direction",
    "MomentumStance",
    "PatternHit",
    "Trend",
    "VolumeStance",
    "VolumeSummary",
]
