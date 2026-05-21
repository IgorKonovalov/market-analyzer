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

from pydantic import BaseModel, ConfigDict

DEFAULT_QUEUE_CAP = 256


class OverlaySpec(BaseModel):
    """Chart overlay descriptor. The literal set is intentionally narrow — adding
    a new kind is additive (new literal value, possibly new optional fields)
    and does NOT bump `chart.show`/`chart.update` payload versions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ema", "sma", "rsi", "macd", "bbands"]
    period: int | None = None


class Marker(BaseModel):
    """`chart.highlight` marker: a single annotation to render on the chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_ts: datetime
    kind: Literal["bullish_marker", "bearish_marker"]
    label: str | None = None


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


class RunCompletedPayloadV1(BaseModel):
    """`run.completed v1` payload: a backtest/analysis/defi artifact is ready."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["backtest", "analysis", "defi"]
    run_id: str
    artifact_path: str


class ChartUpdateDroppedPayloadV1(BaseModel):
    """Synthetic notice emitted when a subscriber's queue overflowed.

    Carries no fields — the renderer's job is to reconcile state when it sees
    this, not to consume the contents of the dropped frames.
    """

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")


TYPE_REGISTRY: dict[str, type[BaseModel]] = {
    "chart.show": ChartShowPayloadV1,
    "chart.update": ChartUpdatePayloadV1,
    "chart.highlight": ChartHighlightPayloadV1,
    "run.completed": RunCompletedPayloadV1,
    "chart.update_dropped": ChartUpdateDroppedPayloadV1,
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
