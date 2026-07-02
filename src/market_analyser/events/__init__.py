"""SSE event bus + typed envelope schema (ADR-0017, Plan 0007 phase 2).

The bus is a small in-process pub/sub: each subscriber gets a bounded
`asyncio.Queue` (default cap 256, configurable for tests). Publishers call
`EventBus.publish(type, payload)` which:

  1. Validates `type` against the registered vocabulary
     (`UnknownEventTypeError` otherwise).
  2. Validates `payload` against the type's per-version Pydantic model
     (Pydantic `ValidationError` otherwise).
  3. Builds an `Envelope` with `ts=now(UTC)` and the registered version.
  4. Fans out to each live subscriber's queue, applying drop-oldest on
     overflow + setting a `dropped` flag so the subscriber's next stream
     iteration yields a synthetic `chart.update_dropped v1` envelope ahead
     of the next real one.

The MCP tools in phase 3 are the publishers. The renderer's `useEventStream`
hook (phase 4, ui-builder) is the consumer.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from market_analyser.advisor.models import Recommendation
from market_analyser.backtest.types import SignalEvaluation

DEFAULT_QUEUE_CAP = 256


class OverlaySpec(BaseModel):
    """Chart overlay descriptor. The literal set is intentionally narrow — adding
    a new kind is additive (new literal value, possibly new optional fields)
    and does NOT bump `chart.show`/`chart.update` payload versions.

    Two families share this one model, kept disjoint by `_validate_kind_fields`:
    the indicator overlays (`ema`/`sma`/`rsi`/`macd`/`bbands`/`supertrend`,
    carrying an optional `period`, plus `supertrend`'s ATR `multiplier`) and the
    generic `price_line` (a horizontal line at `price` with a `label` and an
    optional support/resistance `role`) — the channel the agent uses to push S/R
    levels from `analyze_symbol` (Plan 0047). The new fields are all
    optional/defaulted, so an indicator overlay still serialises to exactly
    `{kind, period}` under the bus's `exclude_none` dump — existing overlays are
    byte-unchanged on the wire.

    `supertrend` (Plan 0049) is additive like `price_line` was: a new indicator
    kind carrying `period` + the optional `multiplier`. The renderer mirror and
    client-side draw land in phase 9 (ui-builder)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ema", "sma", "rsi", "macd", "bbands", "price_line", "supertrend"]
    period: int | None = None
    multiplier: float | None = None  # supertrend's ATR multiplier; None on other kinds
    # `price_line`-only fields (None on indicator overlays, enforced below).
    price: float | None = None
    label: str | None = None
    role: Literal["support", "resistance"] | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> OverlaySpec:
        """Keep the two overlay families disjoint: `price_line` requires both
        `price` and `label` (a labelless line is useless on the chart); the
        indicator kinds accept neither `price`/`label`/`role`."""
        if self.kind == "price_line":
            if self.price is None or self.label is None:
                raise ValueError("price_line overlay requires both 'price' and 'label'")
        elif self.price is not None or self.label is not None or self.role is not None:
            raise ValueError(f"{self.kind} overlay does not accept price/label/role")
        return self


class Marker(BaseModel):
    """`chart.highlight` marker: a single annotation to render on the chart.

    The base shape is a point-in-time arrow at `event_ts` keyed by `kind`
    (bullish/bearish). Plan 0049 (ADR-0045) adds first-class pattern identity and
    an optional bar span, all additively so the existing `highlight_pattern` tool
    keeps emitting `{event_ts, kind, label?}` markers unchanged:

    - `pattern` — the detector name (`"morning_star"`); identity, not the
      free-text `label`. Lets the renderer key dedup on `(event_ts, pattern, kind)`
      so two distinct patterns on the same bar/direction stop collapsing.
    - `kind="neutral_marker"` — so neutral patterns (doji, neutral marubozu) can be
      emitted faithfully; `kind` stays the rendering discriminator.
    - `span_start_ts`/`span_end_ts` — present (together) for a multi-bar pattern's
      span; absent for single-bar patterns (≡ a point marker on `event_ts`).
    - `strength` — the detector's conviction score, so the renderer styles without
      re-deriving it.

    Under the bus's `exclude_none` dump an unset-span point marker still serialises
    to exactly its set fields — the wire stays clean.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_ts: datetime
    kind: Literal["bullish_marker", "bearish_marker", "neutral_marker"]
    label: str | None = None
    pattern: str | None = None
    span_start_ts: datetime | None = None
    span_end_ts: datetime | None = None
    strength: float | None = None

    @model_validator(mode="after")
    def _validate_span(self) -> Marker:
        """A span must be supplied whole and forward-ordered: both endpoints
        together (a half-span is meaningless) and `span_end_ts >= span_start_ts`."""
        if (self.span_start_ts is None) != (self.span_end_ts is None):
            raise ValueError("span requires both span_start_ts and span_end_ts (or neither)")
        if (
            self.span_start_ts is not None
            and self.span_end_ts is not None
            and self.span_end_ts < self.span_start_ts
        ):
            raise ValueError("span_end_ts must be >= span_start_ts")
        return self


class TrendPoint(BaseModel):
    """A single `(time, price)` anchor of a chart trendline (ADR-0049).

    `ts` is a timestamp, not a bar index — consistent with `Marker.event_ts` /
    `span_*_ts`, so the renderer maps it the same way and the line survives a
    bar-set change as long as the anchor times stay in range."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    price: float


class TrendlineSpec(BaseModel):
    """A sloped multi-point line on the chart (ADR-0049, Plan 0052): a
    head-and-shoulders neckline or one bounding trendline of a triangle/wedge.

    Carried as the optional `trendlines` field on `chart.show`/`chart.update`
    payloads — additive and `exclude_none`'d, so payloads without trendlines
    are byte-unchanged on the wire and the payload version does not bump
    (exactly how the `Marker` span fields landed). `style` is the
    forming-vs-confirmed cue (`dashed` = forming, `solid` = confirmed);
    `role`/`pattern` give the renderer theming and identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: list[TrendPoint]
    role: Literal["neckline", "upper_trendline", "lower_trendline"] | None = None
    style: Literal["solid", "dashed"] = "solid"
    label: str | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def _validate_points(self) -> TrendlineSpec:
        """A line needs at least two anchors — a one-point 'line' is undrawable."""
        if len(self.points) < 2:
            raise ValueError("trendline requires at least 2 points")
        return self


class ChartShowPayloadV1(BaseModel):
    """`chart.show v1` payload: render this chart fresh."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    overlays: list[OverlaySpec] | None = None
    trendlines: list[TrendlineSpec] | None = None


class ChartUpdatePayloadV1(BaseModel):
    """`chart.update v1` payload: apply delta to the chart for symbol+timeframe."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    overlays: list[OverlaySpec] | None = None
    trendlines: list[TrendlineSpec] | None = None
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


TYPE_REGISTRY: dict[str, type[BaseModel]] = {
    "chart.show": ChartShowPayloadV1,
    "chart.update": ChartUpdatePayloadV1,
    "chart.highlight": ChartHighlightPayloadV1,
    "run.completed": RunCompletedPayloadV1,
    "signal.evaluated": SignalEvaluatedPayloadV1,
    "recommendation.completed": RecommendationCompletedPayloadV1,
    "chart.update_dropped": ChartUpdateDroppedPayloadV1,
    "ohlcv.backfill_started": OhlcvBackfillStartedPayloadV1,
    "ohlcv.backfilled": OhlcvBackfilledPayloadV1,
    "ohlcv.backfill_failed": OhlcvBackfillFailedPayloadV1,
    "defi.scan_started": DefiScanStartedPayloadV1,
    "defi.scan_progress": DefiScanProgressPayloadV1,
    "defi.scan_completed": DefiScanCompletedPayloadV1,
    "defi.scan_failed": DefiScanFailedPayloadV1,
}


class Envelope(BaseModel):
    """SSE wire envelope. `payload` is the type-specific validated dict."""

    model_config = ConfigDict(frozen=True)

    type: str
    version: int
    ts: datetime
    payload: dict[str, Any]


class UnknownEventTypeError(ValueError):
    """`bus.publish(...)` was called with a type not in TYPE_REGISTRY."""


def _make_dropped_envelope() -> Envelope:
    return Envelope(
        type="chart.update_dropped",
        version=ChartUpdateDroppedPayloadV1.VERSION,
        ts=datetime.now(tz=UTC),
        payload={},
    )


class Subscription:
    """A single subscriber's queue + dropped-flag.

    Acquire via `EventBus.subscribe()`. Iterate via `async for env in
    subscription.stream(): ...` or call `await subscription.next()` for
    single-message reads (used by the SSE route's timeout-driven loop).
    Call `close()` (or drop the iterator) to unsubscribe.
    """

    def __init__(self, bus: EventBus, queue: asyncio.Queue[Envelope]) -> None:
        self._bus = bus
        self._queue = queue
        self._dropped = False
        self._closed = False

    @property
    def queue(self) -> asyncio.Queue[Envelope]:
        return self._queue

    def mark_dropped(self) -> None:
        """Called by the bus when an overflow drops the oldest item."""
        self._dropped = True

    async def next(self) -> Envelope:
        """Return the next envelope, prefixing a synthetic dropped notice if
        the dropped flag is set."""
        if self._dropped:
            self._dropped = False
            return _make_dropped_envelope()
        return await self._queue.get()

    async def stream(self) -> AsyncIterator[Envelope]:
        try:
            while not self._closed:
                yield await self.next()
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._unsubscribe(self)


class EventBus:
    """In-process pub/sub with bounded per-subscriber queues and drop-oldest
    overflow semantics. Not thread-safe — designed for a single asyncio loop."""

    def __init__(self, queue_cap: int = DEFAULT_QUEUE_CAP) -> None:
        self._queue_cap = queue_cap
        self._subscribers: list[Subscription] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> Subscription:
        queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=self._queue_cap)
        sub = Subscription(bus=self, queue=queue)
        self._subscribers.append(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subscribers:
            self._subscribers.remove(sub)

    def publish(self, event_type: str, payload: BaseModel) -> Envelope:
        """Validate + fan out. Raises `UnknownEventTypeError` for an
        unregistered type, `pydantic.ValidationError` for a payload that
        fails its registered model."""
        if event_type not in TYPE_REGISTRY:
            raise UnknownEventTypeError(f"unknown event type: {event_type!r}")
        expected_model = TYPE_REGISTRY[event_type]
        # Re-validate via the registered model so a duck-typed `BaseModel` of
        # the wrong shape still fails at publish time, not at the consumer.
        validated = expected_model.model_validate(payload.model_dump())
        # `VERSION` is a `ClassVar[int]` on every concrete payload model in the
        # registry, but mypy can't see it on the abstract `type[BaseModel]`
        # constraint — bypass via `getattr` (we control all the models in the
        # registry, so the attribute is guaranteed to exist).
        version: int = getattr(expected_model, "VERSION")  # noqa: B009
        envelope = Envelope(
            type=event_type,
            version=version,
            ts=datetime.now(tz=UTC),
            # `exclude_none=True` keeps unset optional fields out of the wire
            # JSON — phase-3 `update_chart` relies on this so a call without
            # `range_start`/`range_end` produces a payload that doesn't carry
            # those keys at all (rather than `null`).
            payload=validated.model_dump(mode="json", exclude_none=True),
        )
        self._fan_out(envelope)
        return envelope

    def _fan_out(self, envelope: Envelope) -> None:
        for sub in list(self._subscribers):  # copy: handlers may mutate list
            self._enqueue_with_overflow(sub, envelope)

    def _enqueue_with_overflow(self, sub: Subscription, envelope: Envelope) -> None:
        q = sub.queue
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            sub.mark_dropped()
        try:
            q.put_nowait(envelope)
        except asyncio.QueueFull:
            # Cap was already reached and we couldn't drop — should not happen
            # under normal flow because we just dropped one above, but stay
            # defensive: mark dropped so the consumer at least knows.
            sub.mark_dropped()
