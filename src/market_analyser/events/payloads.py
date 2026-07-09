"""Typed SSE event payload schemas + the event-type registry (ADR-0017).

The ~20 per-version Pydantic envelope schemas the `EventBus` validates against,
plus the `TYPE_REGISTRY` that maps each wire type string to its payload model.
The shared chart value-types the `chart.*` schemas compose (`OverlaySpec`,
`Marker`, `TrendPoint`, `TrendlineSpec`) live in `events/chart_types.py`.

Split out of `events/__init__.py` in Plan 0072 phase 2 so the schema vocabulary
and the pub/sub runtime (`events/bus.py`) live apart; `events/__init__.py`
re-exports everything so `from market_analyser.events import EventBus, <payload>`
is unchanged. Introspected by `apiref` (the events surface) via `TYPE_REGISTRY`.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.advisor.models import Recommendation
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.events.chart_types import Marker, OverlaySpec, TrendlineSpec
from market_analyser.forecast.result import MultiHorizonForecastResult


class ChartShowPayloadV1(BaseModel):
    """`chart.show v1` payload: render this chart fresh."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    overlays: list[OverlaySpec] | None = None


class ChartUpdatePayloadV1(BaseModel):
    """`chart.update v1` payload: apply delta to the chart for symbol+timeframe."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    overlays: list[OverlaySpec] | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    focus_bar: datetime | None = None


class ChartHighlightPayloadV1(BaseModel):
    """`chart.highlight v1` payload: render markers on a chart."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    markers: list[Marker]


class ChartTrendlinesPayloadV1(BaseModel):
    """`chart.trendlines v1` payload: layer sloped pattern lines onto the chart
    already showing `symbol`/`timeframe` (ADR-0059, Plan 0064).

    Trendlines live on their OWN channel — not on `chart.show`/`chart.update` —
    so a plain `chart.show` can no longer wipe them and they are recomputed from
    current bars (never persisted). Active-chart-gated in the renderer exactly
    like `chart.highlight`: the reducer applies it only when `symbol`+`timeframe`
    match the chart on screen."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    trendlines: list[TrendlineSpec]


class RunCompletedPayloadV1(BaseModel):
    """`run.completed v1` payload: a backtest/analysis/defi artifact is ready."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["backtest", "analysis", "defi"]
    run_id: str
    artifact_path: str


class SignalEvaluatedPayloadV1(BaseModel):
    """`signal.evaluated v1` payload (Plan 0026): the live signal state of one
    strategy on one symbol.

    Unlike `run.completed` (which carries identifiers and lets the renderer fetch
    the large persisted `BacktestResult` via a GET route), this payload rides the
    full `SignalEvaluation` inline — it is small and ephemeral (nothing is
    persisted), so the viewer needs no follow-up fetch. A *condition report*,
    never a recommendation."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation: SignalEvaluation


class RecommendationCompletedPayloadV1(BaseModel):
    """`recommendation.completed v1` payload (Plan 0039, ADR-0029): the advisor
    produced a labeled advisory `Recommendation` for one symbol/timeframe.

    Like `signal.evaluated` (and unlike `run.completed`), the full model rides
    inline: a recommendation is small and ephemeral — nothing is persisted, so
    the viewer needs no follow-up fetch. The `Recommendation` model itself
    enforces the advisory shape structurally (the `label` can only be
    `"advisory"`, a basis always travels with the call), so anything this
    payload validates is safe to render as advice-and-only-advice."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation: Recommendation


class ForecastCompletedPayloadV1(BaseModel):
    """`forecast.completed v1` payload (Plan 0037, ADR-0030/ADR-0054): the
    `forecast` tool produced a multi-horizon forecast.

    Like `signal.evaluated` and `recommendation.completed`, the full result
    rides inline — small and ephemeral, nothing is persisted for the viewer to
    follow-up fetch. One envelope per tool call, however many horizons; a
    horizon that failed the baseline gate travels inside its block with null
    probabilities (the honest no-edge verdict is carried, never suppressed —
    ADR-0030 invariants 3/4). A *condition report* (a calibrated probability),
    never a recommendation (ADR-0029)."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    forecast: MultiHorizonForecastResult


class ChartUpdateDroppedPayloadV1(BaseModel):
    """Synthetic notice emitted when a subscriber's queue overflowed.

    Carries no fields — the renderer's job is to reconcile state when it sees
    this, not to consume the contents of the dropped frames.
    """

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")


class GapWindow(BaseModel):
    """A single `[start, end]` coverage gap the backfill is (or was) filling.
    Shared by the `ohlcv.backfill_started` event and the `backfill_ohlcv` tool
    response (Plan 0013)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: datetime
    end: datetime


class OhlcvBackfillStartedPayloadV1(BaseModel):
    """`ohlcv.backfill_started v1`: a backfill fetch began for symbol+timeframe.
    Emitted before the upstream call so the renderer can show its spinner."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    gaps: list[GapWindow]


class OhlcvBackfilledPayloadV1(BaseModel):
    """`ohlcv.backfilled v1`: a backfill completed; the cache is now hot for the
    `[range_start, range_end]` span. The renderer refetches `/ohlcv` on this."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    bars_added: int


class OhlcvBackfillFailedPayloadV1(BaseModel):
    """`ohlcv.backfill_failed v1`: a backfill failed with a typed reason. The
    literal set is closed so the renderer can branch on it exhaustively."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    reason: Literal["rate_limited", "upstream_unavailable", "unknown_symbol", "history_exceeded"]
    message: str


class DefiScanStartedPayloadV1(BaseModel):
    """`defi.scan_started v1`: a wallet scan began. Emitted before the upstream
    call so the renderer can show its spinner. `wallet` is the **masked** address
    (`0x1234…abcd`) — the full address is never put on the wire (ADR-0038
    discipline). `chains` is the set of chains being scanned."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    chains: list[str]


class DefiScanProgressPayloadV1(BaseModel):
    """`defi.scan_progress v1`: positions decoded for one chain. At least one is
    emitted between `scan_started` and `scan_completed` for a non-empty wallet."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    chain: str
    position_count: int


class DefiScanCompletedPayloadV1(BaseModel):
    """`defi.scan_completed v1`: the scan finished. `chains` is the chains where
    positions were found; `position_count` is the total across all chains."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    chains: list[str]
    position_count: int


class DefiScanFailedPayloadV1(BaseModel):
    """`defi.scan_failed v1`: the scan failed with a typed reason. The literal set
    is closed so the renderer can branch on it exhaustively. A missing/invalid
    key and any other upstream outage both surface as `upstream_unavailable` on
    the wire; the precise auth signal reaches the agent through the scan tool's
    re-raised typed exception (phase 4), keeping this neutral payload decoupled
    from any one source's error taxonomy."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    reason: Literal["rate_limited", "upstream_unavailable", "malformed_response"]
    message: str


class DefiPnlStartedPayloadV1(BaseModel):
    """`defi.pnl_started v1`: a wallet P&L reconstruction began (Plan 0035).
    `wallet` is the **masked** address — the full address never reaches the
    wire (ADR-0038 discipline)."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str


class DefiPnlCompletedPayloadV1(BaseModel):
    """`defi.pnl_completed v1`: the reconstruction finished. Totals are `None`
    whenever any position is `incomplete` — the wire carries the same honesty
    the engine does (never a confident partial number, ADR-0036)."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    position_count: int
    incomplete_count: int
    realized_usd: float | None
    unrealized_usd: float | None


class DefiPnlFailedPayloadV1(BaseModel):
    """`defi.pnl_failed v1`: the reconstruction failed with a typed reason (the
    scan-failed literal set — same closed vocabulary, same neutrality: the
    precise auth error reaches the caller through the job's re-raised typed
    exception, not the wire)."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    reason: Literal["rate_limited", "upstream_unavailable", "malformed_response"]
    message: str


class AlertTriggeredPayloadV1(BaseModel):
    """`alert.triggered v1` payload (Plan 0060, ADR-0055): a watch's condition
    transitioned false→true on its latest evaluation.

    **Condition-only** by construction: the triggering fact (`condition`, a
    human-readable statement like ``rsi 28.44 < 30``), the numbers behind it
    (`values`), and identity/timing fields. Deliberately absent: direction,
    action, conviction, side, size — an alert from a background loop must
    never cross the ADR-0029 advisory boundary (`extra="forbid"` plus the
    schema test in `tests/alerts/test_scheduler.py` pin this).
    """

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    watch_id: int
    symbol: str
    timeframe: str
    kind: Literal["indicator_threshold", "pattern", "strategy_signal"]
    fired_at: datetime
    condition: str
    values: dict[str, float]


TYPE_REGISTRY: dict[str, type[BaseModel]] = {
    "chart.show": ChartShowPayloadV1,
    "chart.update": ChartUpdatePayloadV1,
    "chart.highlight": ChartHighlightPayloadV1,
    "chart.trendlines": ChartTrendlinesPayloadV1,
    "run.completed": RunCompletedPayloadV1,
    "signal.evaluated": SignalEvaluatedPayloadV1,
    "recommendation.completed": RecommendationCompletedPayloadV1,
    "forecast.completed": ForecastCompletedPayloadV1,
    "chart.update_dropped": ChartUpdateDroppedPayloadV1,
    "ohlcv.backfill_started": OhlcvBackfillStartedPayloadV1,
    "ohlcv.backfilled": OhlcvBackfilledPayloadV1,
    "ohlcv.backfill_failed": OhlcvBackfillFailedPayloadV1,
    "defi.scan_started": DefiScanStartedPayloadV1,
    "defi.scan_progress": DefiScanProgressPayloadV1,
    "defi.scan_completed": DefiScanCompletedPayloadV1,
    "defi.scan_failed": DefiScanFailedPayloadV1,
    "defi.pnl_started": DefiPnlStartedPayloadV1,
    "defi.pnl_completed": DefiPnlCompletedPayloadV1,
    "defi.pnl_failed": DefiPnlFailedPayloadV1,
    "alert.triggered": AlertTriggeredPayloadV1,
}


__all__ = [
    "TYPE_REGISTRY",
    "AlertTriggeredPayloadV1",
    "ChartHighlightPayloadV1",
    "ChartShowPayloadV1",
    "ChartTrendlinesPayloadV1",
    "ChartUpdateDroppedPayloadV1",
    "ChartUpdatePayloadV1",
    "DefiPnlCompletedPayloadV1",
    "DefiPnlFailedPayloadV1",
    "DefiPnlStartedPayloadV1",
    "DefiScanCompletedPayloadV1",
    "DefiScanFailedPayloadV1",
    "DefiScanProgressPayloadV1",
    "DefiScanStartedPayloadV1",
    "ForecastCompletedPayloadV1",
    "GapWindow",
    "OhlcvBackfillFailedPayloadV1",
    "OhlcvBackfillStartedPayloadV1",
    "OhlcvBackfilledPayloadV1",
    "RecommendationCompletedPayloadV1",
    "RunCompletedPayloadV1",
    "SignalEvaluatedPayloadV1",
]
