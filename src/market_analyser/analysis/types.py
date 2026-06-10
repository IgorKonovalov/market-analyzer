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

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["bullish", "bearish", "neutral"]


class PatternHit(BaseModel):
    """A candlestick pattern detected at a specific bar.

    `bar_index` is the index of the *latest* bar of the formation in the input
    series (the bar at which the pattern completes). `strength` is a detector-
    defined score in `[0, 1]` — relative conviction, not a probability.

    `span_bars` is the statically-known number of bars the formation occupies
    (1 for single-bar patterns, 2/3 for the multi-bar ones), ending on
    `bar_index`. It is bookkeeping, not new analysis — a doji always spans 1, a
    morning star always spans 3 — and it lets a sweep resolve the formation's
    `(start_ts, end_ts)` for span rendering (Plan 0049, ADR-0045). The span
    reaches back from `bar_index`, so no future bar is involved: the
    anti-lookahead guarantee is unchanged.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bar_index: int
    pattern: str
    direction: Direction
    strength: float
    span_bars: int = Field(ge=1, le=3)


class Pivot(BaseModel):
    """A confirmed swing pivot in a bar series (Plan 0051 phase 1).

    A `high` pivot is a bar whose high strictly exceeds the highs of the `left`
    bars before it and the `right` bars after it (a resistance pivot); a `low`
    pivot is the mirror on lows (a support pivot). `bar_index` is the bar at
    which the extreme PRINTED — but the pivot is only *confirmed* (and therefore
    only returned by `swing_pivots`) once all `right` bars after it exist, so a
    pivot at bar `j` is first knowable at bar `j + right`. No future bar beyond
    the series end is ever read (anti-lookahead, ADR-0023).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bar_index: int
    ts: datetime
    price: float
    kind: Literal["high", "low"]  # high -> resistance pivot, low -> support pivot


class Level(BaseModel):
    """A clustered, strength-ranked support/resistance zone (Plan 0051 phase 3).

    `price` is the representative price of the clustered pivot zone (the mean
    of the clustered pivot prices); `touches` is how many pivots the cluster
    absorbed; `volume_at_level` is the summed volume-by-price mass inside the
    zone's price band (the phase-2 profile); `strength` is a 0..1 rank blending
    touch count and volume-at-level (the documented formula and its named
    weights live in `analysis/levels.py`). `first_ts`/`last_ts` bound the
    pivots in the cluster. Conditions only — a level is chart geometry, never
    a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: float
    role: Literal["support", "resistance"]
    touches: int
    volume_at_level: float
    strength: float
    first_ts: datetime
    last_ts: datetime


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


class VolumeBreakout(BaseModel):
    """Whether the latest bar broke its trailing price range on a volume surge
    (Plan 0021 phase 2). `is_breakout` is true only when both legs fire: volume
    at least `vol_multiple` times its trailing average AND the close clearing the
    trailing high (`direction="bullish"`) or low (`direction="bearish"`).
    `broken_level` is that cleared extreme, or ``None`` when there is no breakout.
    `volume_multiple` is the latest relative-volume ratio (``None`` when too few
    bars). Conditions only — never a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    is_breakout: bool
    direction: Direction
    volume_multiple: float | None
    broken_level: float | None


class VolumeConfirmation(BaseModel):
    """How well volume backs the recent price move (Plan 0021 phase 2).

    Over the trailing window, `score` (0..1) is the share of directional volume
    sitting on bars that move *with* the net price direction — high when the move
    is carried by trend-aligned volume, low when volume concentrates on the
    counter-trend bars (a divergence). `confirmed` is `score` at or above the
    explicit threshold; `direction` is the net price direction over the window.
    Conditions only — never a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    score: float  # 0..1 share of directional volume aligned with the net move
    confirmed: bool
    direction: Direction
    supportive_volume: float
    opposing_volume: float


class SmartVolumeHit(BaseModel):
    """A combined volume-surge-with-RSI-in-band condition (Plan 0021 phase 2).

    `qualifies` is true when relative volume is at least `vol_multiple` times its
    trailing average AND the latest RSI sits inside `[rsi_low, rsi_high]`.
    `volume_multiple` / `rsi` carry the latest figures (``None`` when undefined
    over the available bars). Conditions only — never a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    qualifies: bool
    volume_multiple: float | None
    rsi: float | None


class ConditionSnapshot(BaseModel):
    """A composed, point-in-time technical condition read over cached bars.

    Conditions only — no buy/sell/action field, by the analyst non-negotiable.
    `indicators` carries the latest values keyed by name (e.g. ``rsi``, ``macd``,
    ``bb_pct_b``, ``atr``, ``adx``, ``supertrend_direction``, plus the trailing
    percentile ranks ``rsi_pct90`` / ``atr_pct90``); a value is ``None`` when the
    indicator is undefined over the available bars. `support_resistance` maps
    ``"support"`` / ``"resistance"`` to trailing swing levels. `volume_stance` is
    the coarse volume reading (heavy/normal/light); the numeric volume measures
    (``volume``, ``vol_sma20``, ``rel_volume``, ``vol_pct90``, ``obv``,
    ``obv_slope``, ``vwap``) ride in `indicators` alongside the others.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of: datetime
    trend: Trend
    momentum: MomentumStance
    volume_stance: VolumeStance
    indicators: dict[str, float | None]
    support_resistance: dict[str, list[float]]
    recent_patterns: list[PatternHit]


class TimeframeView(BaseModel):
    """One timeframe's condition read inside a multi-timeframe alignment (Plan 0021).

    `snapshot` is ``None`` when no bars were available for the timeframe — an
    honest per-timeframe gap, not a failure of the whole alignment.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    snapshot: ConditionSnapshot | None


class MultiTimeframeAlignment(BaseModel):
    """Whether one symbol's trend agrees across a ladder of timeframes (Plan 0021).

    `timeframes` carries each timeframe's `ConditionSnapshot` (in the order the
    caller supplied), so a timeframe whose `snapshot.trend` differs from
    `dominant_trend` is named by the view itself. `dominant_trend` is the trend
    held by the most timeframes (ties broken deterministically toward up→down→
    sideways), falling back to `SIDEWAYS` when no timeframe has bars. `agreement`
    is the fraction of *available* timeframes whose trend equals `dominant_trend`
    (0..1; `0.0` when none are available). Conditions only — no buy/sell field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframes: list[TimeframeView]
    dominant_trend: Trend
    agreement: float  # 0..1 fraction of available timeframes agreeing


__all__ = [
    "ConditionSnapshot",
    "Direction",
    "Level",
    "MomentumStance",
    "MultiTimeframeAlignment",
    "PatternHit",
    "Pivot",
    "SmartVolumeHit",
    "TimeframeView",
    "Trend",
    "VolumeBreakout",
    "VolumeConfirmation",
    "VolumeStance",
    "VolumeSummary",
]
