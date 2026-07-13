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


PatternState = Literal["forming", "confirmed"]


class PivotPoint(BaseModel):
    """A `(time, price)` anchor of a classical chart pattern (Plan 0052).

    Unlike `Pivot` this is pure geometry — no `bar_index`, no high/low kind —
    because it doubles as the anchor shape the chart's trendline primitive
    consumes (ADR-0049 maps `ts` through the time scale, `price` through the
    candle series' price scale).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    price: float


class LineSeg(BaseModel):
    """A defining line segment of a classical chart pattern (Plan 0052).

    `role` names what the segment is in the formation: the neckline of an
    H&S / double top-bottom, or one of the two bounding trendlines of a
    triangle / wedge. The segment's endpoints sit on real pivot anchors
    (connect-the-extremes, ADR-0048) — never on a fitted line off the prices.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: PivotPoint
    end: PivotPoint
    role: Literal["neckline", "upper_trendline", "lower_trendline", "projection", "base"]


class ChartPatternHit(BaseModel):
    """A classical chart pattern detected over confirmed swing pivots
    (Plan 0052, ADR-0048).

    `state` is the two-state trailing lifecycle: `forming` is emitted at the
    bar where the geometry first completes (every defining pivot confirmed),
    `confirmed` at the bar whose close breaks the neckline / breakout
    trendline by the volatility-scaled margin (`k * ATR`). `bar_index` is that
    completing / confirming bar — the bar at which the hit is first knowable,
    so a hit reported at bar `i` is byte-identical on `bars[0..=i]` (the
    anti-lookahead invariant pinned in `tests/analysis/test_chart_patterns.py`).

    `pivots` are the ordered defining anchors; `lines` the neckline or the two
    bounding trendlines; `target` the textbook measured-move projection (a
    geometry fact, never advice — there is no action/buy/sell field, the
    analyst non-negotiable); `strength` a detector-defined 0..1 relative
    conviction, not a probability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str
    state: PatternState
    direction: Direction
    bar_index: int
    pivots: list[PivotPoint]
    lines: list[LineSeg]
    target: float | None
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


class CounterTrendBar(BaseModel):
    """One bar's contribution to the counter-trend volume decomposition
    (Plan 0090, ADR-0083).

    `direction` is the bar's own up/down/flat read (`bullish` = close above open,
    `bearish` = close below open, `neutral` = doji), a purely trailing per-bar
    fact. `relative_volume` is the bar's volume ÷ its trailing volume MA (``None``
    when the MA is undefined over the available history, or zero).
    `is_counter_trend` is true when the bar's direction opposes the *anchor* trend
    (a down-bar under an ``up`` trend, an up-bar under a ``down`` trend) — always
    false when the anchor is ``sideways`` (there is no trend to run counter to).
    Conditions only — never a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    direction: Direction
    relative_volume: float | None
    is_counter_trend: bool


class CounterTrendVolume(BaseModel):
    """A per-bar counter-trend volume decomposition anchored to the snapshot's
    canonical `trend` (Plan 0090, ADR-0083).

    Over the trailing `lookback` bars, each bar is classified with-trend or
    counter-trend **relative to the supplied `trend`** — the same EMA/ADX +
    Ichimoku-veto label the snapshot reports, so "counter-trend" has one definition
    across the surface (unlike `VolumeConfirmation`'s net-move anchor, which is left
    unchanged). `counter_trend_volume_share` is the share of *directional* volume
    (neutral bars excluded) sitting on the counter-trend bars — high when the move
    is fought by heavy opposing volume (a divergence). When the anchor `trend` is
    ``sideways`` there is nothing to run counter to: `anchored_to_sideways` is true,
    every bar's `is_counter_trend` is false, and `counter_trend_volume_share` is
    ``None`` (undefined, never forced onto a net-move sign). Conditions only — never
    a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    trend: Trend  # the anchor — the snapshot's canonical trend
    lookback: int
    anchored_to_sideways: bool
    bars: list[CounterTrendBar]
    counter_trend_volume_share: float | None  # None iff anchored_to_sideways


DivergenceKind = Literal["regular_bullish", "regular_bearish", "hidden_bullish", "hidden_bearish"]


class Divergence(BaseModel):
    """A price↔oscillator divergence over confirmed swing pivots (Plan 0091).

    Pairs the two most recent confirmed price pivots of one kind against the
    oscillator's own pivots and classifies the disagreement between the price
    slope and the oscillator slope:

    * ``regular_bearish`` — price higher high, oscillator lower high (a rally
      losing momentum);
    * ``regular_bullish`` — price lower low, oscillator higher low (a decline
      losing momentum);
    * ``hidden_bearish`` — price lower high, oscillator higher high (trend
      continuation warning, down);
    * ``hidden_bullish`` — price higher low, oscillator lower low (trend
      continuation, up).

    `price_pivots` are the two `(ts, price)` price anchors (older first);
    `oscillator_pivots` the two matched oscillator anchors, whose ``price`` field
    carries the oscillator *value* at that pivot (the y-coordinate on the
    oscillator pane, the same geometry `PivotPoint` the trendline primitive
    consumes). `bar_index` is the confirming bar — the bar at which the *later* of
    the four pivots is first confirmed, so a divergence reported at bar ``i`` is
    byte-identical on ``bars[0..=i]`` (trailing, anti-lookahead, ADR-0023).
    `strength` is a detector-defined 0..1 relative magnitude (blending the price
    and oscillator slope fractions), **not** a probability. Conditions only — a
    divergence is chart geometry, never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    oscillator: Literal["rsi", "macd_hist", "obv", "mfi"]
    kind: DivergenceKind
    price_pivots: list[PivotPoint]
    oscillator_pivots: list[PivotPoint]
    bar_index: int
    strength: float


class FibonacciLevels(BaseModel):
    """A Fibonacci retracement or extension grid over one confirmed swing (Plan 0092).

    Chart geometry — the canonical "where does a pullback-in-trend find support /
    where does the move extend" grid, drawn between two swing anchors. `kind` is
    ``retracement`` (levels *inside* the swing, `high_anchor`↔`low_anchor`) or
    ``extension`` (levels *beyond* the swing, projected from a pullback). The
    `direction` is the swing's own direction, inferred from the anchors' temporal
    order: ``bullish`` when the low printed before the high (an up-swing, retracing
    down from the high), ``bearish`` when the high printed first (a down-swing,
    retracing up from the low). `levels` maps each ratio (as a string key, e.g.
    ``"0.618"``) to its price; the mapping is oriented by `direction` so ratio 0
    sits at the swing's end and ratio 1 at its start. Trailing by construction —
    the anchors come from confirmed `swing_pivots`, so no future bar is read
    (ADR-0023). Conditions only — a fib grid is geometry, never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["retracement", "extension"]
    high_anchor: PivotPoint
    low_anchor: PivotPoint
    direction: Direction  # swing direction the grid is drawn for
    levels: dict[str, float]  # {"0.382": ..., "0.5": ..., "0.618": ...}


StructureLabel = Literal["HH", "HL", "LH", "LL"]
StructureEventKind = Literal["BOS", "CHoCH"]


class StructureEvent(BaseModel):
    """A break-of-structure (BOS) or change-of-character (CHoCH) event over the
    confirmed swing sequence (Plan 0092, ADR-0084).

    A ``BOS`` is a swing extreme taken out *in the trend direction* (continuation);
    a ``CHoCH`` is the *first counter-trend* break — the earliest sign the trend's
    character is changing. `direction` is the break's direction (``bullish`` = an
    upside break of a swing high, ``bearish`` = a downside break of a swing low).
    `bar_index` is the bar whose close first takes out the referenced level (the
    bar at which the event is first knowable — the level is a confirmed prior
    pivot, the close is known at that bar, so no future data is read). `price` is
    the level that was broken. Conditions only — never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: StructureEventKind
    direction: Direction
    bar_index: int
    price: float


class MarketStructure(BaseModel):
    """The price-action market-structure read — a second, distinct trend lens
    reported *alongside* the composed indicator `trend`, never merged into it
    (Plan 0092, ADR-0084).

    `structural_trend` is derived purely from the labeled swing sequence: ``up``
    when the latest structure is a higher-high **and** a higher-low, ``down`` when
    it is a lower-high **and** a lower-low, ``range`` otherwise. It is deliberately
    a plain string literal (``up``/``down``/``range``), not the indicator `Trend`
    enum — the two are separate facts and may legitimately disagree (that
    disagreement is itself the signal, ADR-0084). `labeled_pivots` pairs each
    confirmed swing pivot that has a same-kind predecessor with its HH/HL/LH/LL
    label (ordered by bar). `events` are the BOS/CHoCH structural breaks in bar
    order. Trailing by construction — labels and events read only confirmed pivots
    and closes at-or-before their bar, so the read at bar ``i`` is byte-identical
    on ``bars[0..=i]`` (ADR-0023). Conditions only — never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    structural_trend: Literal["up", "down", "range"]  # ADR-0084: distinct from `trend`
    labeled_pivots: list[tuple[PivotPoint, StructureLabel]]
    events: list[StructureEvent]


class PivotPoints(BaseModel):
    """Classic floor-trader / Camarilla / Woodie pivot levels (Plan 0092).

    Static horizontal support/resistance derived from the prior completed period's
    high/low/close (the last completed bar of the series' timeframe). `pivot` is the
    central level; `resistances` is ``[R1, R2, R3]`` and `supports` is ``[S1, S2,
    S3]``, always three each in ascending index (R1 nearest the pivot). The formula
    set is selected by `method`. Trailing — reads only the last completed bar, no
    future data (ADR-0023). Conditions only — chart geometry, never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["floor", "camarilla", "woodie"]
    pivot: float
    resistances: list[float]  # R1, R2, R3
    supports: list[float]  # S1, S2, S3


class AnchoredVwapValue(BaseModel):
    """The latest anchored VWAP for one symbol, anchored to a chosen bar (Plan 0092).

    Anchored VWAP is the volume-weighted average of the typical price
    ``(high + low + close) / 3`` accumulated from an *anchor* bar (a swing or event)
    to the last bar — dynamic support/resistance that, unlike the rolling
    `VolumeSummary.vwap`, has a fixed start. `anchor_index` / `anchor_ts` are the
    anchor's position and timestamp (provenance the renderer re-anchors from);
    `value` is the latest anchored VWAP, or ``None`` when the volume accumulated
    from the anchor is zero (degenerate — no weighting defined, never a
    divide-by-zero). Trailing by construction: the value at bar ``i`` reads only
    ``anchor..i``. Conditions only — never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_index: int
    anchor_ts: datetime
    value: float | None


class NearestFibLevel(BaseModel):
    """The single Fibonacci retracement level nearest the last close (Plan 0092).

    A compact condition summary drawn from the dominant-swing auto-anchored
    retracement grid — "price is sitting near the 0.618". `ratio` is the level's
    ratio key (e.g. ``"0.618"``), `price` its price, `direction` the anchoring
    swing's direction. ``None`` on the snapshot when there is no dominant swing to
    anchor to. Conditions only — geometry, never a buy/sell call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ratio: str
    price: float
    direction: Direction


class ConditionSnapshot(BaseModel):
    """A composed, point-in-time technical condition read over cached bars.

    Conditions only — no buy/sell/action field, by the analyst non-negotiable.
    `indicators` carries the latest values keyed by name (e.g. ``rsi``, ``macd``,
    ``bb_pct_b``, ``atr``, ``adx``, ``supertrend_direction``, the Ichimoku scalars
    ``ichimoku_tenkan`` / ``ichimoku_kijun`` and the displaced cloud-under-price
    ``ichimoku_cloud_a`` / ``ichimoku_cloud_b`` (spans computed ``displacement``
    bars ago, ADR-0067), the squeeze trio ``bb_width`` /  ``bb_width_pct90`` (the
    canonical compression metric — Bollinger band-width and its trailing percentile,
    ADR-0083) and ``squeeze_on`` (``1.0``/``0.0``, TTM Bollinger-inside-Keltner on
    the latest bar, categorical-as-float like ``supertrend_direction``), plus the
    trailing percentile ranks ``rsi_pct90`` / ``atr_pct90``); a value is ``None``
    when the indicator is undefined over the
    available bars. `trend` folds the Ichimoku cloud into the EMA/ADX read as a
    conjunctive veto (ADR-0067): a divergence between the moving-average stack and
    the cloud resolves to ``SIDEWAYS`` rather than a directional label. `support_resistance` maps
    ``"support"`` / ``"resistance"`` to trailing swing levels. `nearest_support`
    / `nearest_resistance` (Plan 0051 phase 4) are the structured clustered
    `Level`s nearest the last close — the support at-or-below it and the
    resistance at-or-above it, each carrying its strength — or ``None`` when no
    level sits on that side. `volume_stance` is the coarse volume reading
    (heavy/normal/light); the numeric volume measures (``volume``,
    ``vol_sma20``, ``rel_volume``, ``vol_pct90``, ``obv``, ``obv_slope``,
    ``vwap``) ride in `indicators` alongside the others, as do the Plan-0091
    momentum oscillators (``stoch_k`` / ``stoch_d`` / ``stoch_rsi`` / ``cci`` /
    ``williams_r`` / ``roc``) and money-flow gauges (``mfi`` / ``ad_line`` /
    ``cmf``) — reported latest values, not a re-vote of the ``momentum`` stance
    (which stays RSI-zone + MACD, ADR-0023). `active_patterns`
    (Plan 0052 phase 3) carries the classical chart patterns still in play —
    the latest-state `ChartPatternHit` per formation whose completing /
    confirming bar falls inside the trailing activity window (the breakout
    scan horizon) — empty when nothing is forming or freshly confirmed.
    `recent_divergences` (Plan 0091) carries the price↔oscillator `Divergence`s
    whose confirming bar falls inside the trailing recent-activity window, across
    the oscillator set (RSI / MACD-hist / OBV / MFI) — empty when none are active.
    `market_structure` (Plan 0092, ADR-0084) is the price-action HH/HL/LH/LL read —
    a *second, distinct* trend lens reported **alongside** the untouched `trend`,
    never merged into it (the two may legitimately disagree). `nearest_fib_level`
    (Plan 0092) is the retracement level nearest the last close from the
    dominant-swing grid, or ``None`` when there is no dominant swing to anchor to.
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
    nearest_support: Level | None
    nearest_resistance: Level | None
    recent_patterns: list[PatternHit]
    active_patterns: list[ChartPatternHit]
    recent_divergences: list[Divergence]
    market_structure: MarketStructure  # ADR-0084: a distinct trend read beside `trend`
    nearest_fib_level: NearestFibLevel | None


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
    "AnchoredVwapValue",
    "ChartPatternHit",
    "ConditionSnapshot",
    "CounterTrendBar",
    "CounterTrendVolume",
    "Direction",
    "Divergence",
    "DivergenceKind",
    "FibonacciLevels",
    "Level",
    "LineSeg",
    "MarketStructure",
    "MomentumStance",
    "MultiTimeframeAlignment",
    "NearestFibLevel",
    "PatternHit",
    "PatternState",
    "Pivot",
    "PivotPoint",
    "PivotPoints",
    "SmartVolumeHit",
    "StructureEvent",
    "StructureEventKind",
    "StructureLabel",
    "TimeframeView",
    "Trend",
    "VolumeBreakout",
    "VolumeConfirmation",
    "VolumeStance",
    "VolumeSummary",
]
