"""Plan 0013 phase 1 done-when: the three `ohlcv.backfill_*` event types.

Defends, at the EventBus level (no HTTP server needed — the wire/SSE path is
already covered by test_events_sse.py):
- `ohlcv.backfill_started` / `ohlcv.backfilled` / `ohlcv.backfill_failed` each
  publish with `version == 1`, the right `type`, and fan out to a subscriber
  unchanged.
- Boundary validation at publish time: a `bars_added` that isn't an int, and a
  `backfill_failed` `reason` outside the closed literal set, both raise
  `pydantic.ValidationError`.
- Every published envelope's payload is JSON-serialisable with `datetime` fields
  rendered as ISO-8601 strings.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from market_analyser.api.events import (
    Envelope,
    EventBus,
    GapWindow,
    OhlcvBackfilledPayloadV1,
    OhlcvBackfillFailedPayloadV1,
    OhlcvBackfillStartedPayloadV1,
)

_T0 = datetime(2026, 5, 1, tzinfo=UTC)
_T1 = datetime(2026, 5, 20, tzinfo=UTC)


def _publish_and_receive(event_type: str, payload: BaseModel) -> tuple[Envelope, Envelope]:
    """Publish on a fresh bus with one subscriber; return (published, received)."""

    async def run() -> tuple[Envelope, Envelope]:
        bus = EventBus()
        sub = bus.subscribe()
        published = bus.publish(event_type, payload)
        received = await sub.next()
        sub.close()
        return published, received

    return asyncio.run(run())


def test_backfill_started_publishes_and_fans_out() -> None:
    payload = OhlcvBackfillStartedPayloadV1(
        symbol="MSFT",
        timeframe="1d",
        gaps=[GapWindow(start=_T0, end=_T1)],
    )
    published, received = _publish_and_receive("ohlcv.backfill_started", payload)

    assert published.type == "ohlcv.backfill_started"
    assert published.version == 1
    assert received.type == published.type
    assert received.version == published.version
    assert received.payload == published.payload
    assert received.payload["symbol"] == "MSFT"
    # datetimes render as ISO-8601 strings (pydantic JSON mode uses a trailing Z)
    (gap,) = received.payload["gaps"]
    assert gap["start"].startswith("2026-05-01T00:00:00")
    assert gap["end"].startswith("2026-05-20T00:00:00")


def test_backfilled_publishes_and_fans_out() -> None:
    payload = OhlcvBackfilledPayloadV1(
        symbol="AAPL",
        timeframe="1d",
        range_start=_T0,
        range_end=_T1,
        bars_added=14,
    )
    published, received = _publish_and_receive("ohlcv.backfilled", payload)

    assert published.type == "ohlcv.backfilled"
    assert published.version == 1
    assert received.payload == published.payload
    assert received.payload["bars_added"] == 14
    assert received.payload["range_start"].startswith("2026-05-01T00:00:00")


def test_backfill_failed_publishes_and_fans_out() -> None:
    payload = OhlcvBackfillFailedPayloadV1(
        symbol="NVDA",
        timeframe="1h",
        reason="rate_limited",
        message="yahoo: rate limited (HTTP 429)",
    )
    published, received = _publish_and_receive("ohlcv.backfill_failed", payload)

    assert published.type == "ohlcv.backfill_failed"
    assert published.version == 1
    assert received.payload == published.payload
    assert received.payload["reason"] == "rate_limited"
    assert received.payload["message"] == "yahoo: rate limited (HTTP 429)"


def test_backfilled_rejects_non_int_bars_added_at_publish() -> None:
    """A payload whose `bars_added` isn't an int fails the registered model at
    publish time (boundary validation), not at the consumer."""

    class _LooseBackfilled(BaseModel):
        symbol: str = "AAPL"
        timeframe: str = "1d"
        range_start: datetime = _T0
        range_end: datetime = _T1
        bars_added: str = "lots"  # wrong type on purpose

    bus = EventBus()
    with pytest.raises(ValidationError):
        bus.publish("ohlcv.backfilled", _LooseBackfilled())


def test_backfill_failed_rejects_reason_outside_literal_set() -> None:
    """The `reason` literal set is closed — an unknown reason raises at publish."""

    class _LooseFailed(BaseModel):
        symbol: str = "AAPL"
        timeframe: str = "1d"
        reason: str = "something_else"
        message: str = "boom"

    bus = EventBus()
    with pytest.raises(ValidationError):
        bus.publish("ohlcv.backfill_failed", _LooseFailed())


@pytest.mark.parametrize(
    ("event_type", "payload", "expect_iso"),
    [
        (
            "ohlcv.backfill_started",
            OhlcvBackfillStartedPayloadV1(
                symbol="MSFT", timeframe="1d", gaps=[GapWindow(start=_T0, end=_T1)]
            ),
            True,
        ),
        (
            "ohlcv.backfilled",
            OhlcvBackfilledPayloadV1(
                symbol="AAPL", timeframe="1d", range_start=_T0, range_end=_T1, bars_added=3
            ),
            True,
        ),
        (
            "ohlcv.backfill_failed",
            OhlcvBackfillFailedPayloadV1(
                symbol="NVDA", timeframe="1d", reason="unknown_symbol", message="no rows"
            ),
            False,  # this payload carries no datetime field
        ),
    ],
)
def test_published_payload_is_json_serialisable_with_iso_datetimes(
    event_type: str, payload: BaseModel, expect_iso: bool
) -> None:
    bus = EventBus()
    envelope = bus.publish(event_type, payload)
    dumped = json.dumps(envelope.payload)  # must not raise
    # Datetime fields render as ISO-8601 strings (date "2026-05-01" prefix present).
    if expect_iso:
        assert "2026-05-01" in dumped
