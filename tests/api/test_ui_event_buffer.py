"""Plan 0014 phase 1 done-when: the `UIEventBuffer` ring buffer.

Asserts the buffer's contract that the MCP tool (phase 2) and the routes depend
on: append order + drop-oldest overflow, draining vs peeking reads, strict
`since` filtering, the `on_append` seam fires once per append, and concurrent
appends on one asyncio loop neither interleave nor lose envelopes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from market_analyser.api.ui_events import UIEventEnvelope
from market_analyser.api.ui_events.buffer import UIEventBuffer

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _envelope(n: int) -> UIEventEnvelope:
    """A distinct, ordered envelope: ts = base + n minutes, unique uuid4 id."""
    return UIEventEnvelope(
        event_id=str(uuid.uuid4()),
        type="ui.bar_clicked",
        version=1,
        ts=_BASE + timedelta(minutes=n),
        payload={"n": n},
    )


def test_snapshot_returns_appends_in_order_with_identity_preserved() -> None:
    buffer = UIEventBuffer(maxlen=3)
    envelopes = [_envelope(1), _envelope(2), _envelope(3)]
    for env in envelopes:
        buffer.append(env)

    snap = buffer.snapshot()
    assert [e.event_id for e in snap] == [e.event_id for e in envelopes]
    assert [e.ts for e in snap] == [e.ts for e in envelopes]
    assert [e.payload for e in snap] == [e.payload for e in envelopes]
    # Each event_id is a distinct UUID v4 string.
    ids = [e.event_id for e in snap]
    assert len(set(ids)) == 3
    for raw in ids:
        assert uuid.UUID(raw).version == 4


def test_overflow_drops_oldest() -> None:
    buffer = UIEventBuffer(maxlen=3)
    envelopes = [_envelope(i) for i in range(1, 5)]  # 1,2,3,4
    for env in envelopes:
        buffer.append(env)

    snap = buffer.snapshot()
    assert [e.payload["n"] for e in snap] == [2, 3, 4]


def test_drain_none_returns_all_and_empties() -> None:
    buffer = UIEventBuffer(maxlen=10)
    envelopes = [_envelope(i) for i in range(1, 4)]
    for env in envelopes:
        buffer.append(env)

    drained = buffer.drain(since=None)
    assert [e.payload["n"] for e in drained] == [1, 2, 3]
    assert buffer.snapshot() == []


def test_drain_since_is_strict_greater_than_and_retains_older() -> None:
    buffer = UIEventBuffer(maxlen=10)
    envelopes = [_envelope(i) for i in range(1, 5)]  # ts at +1..+4 min
    for env in envelopes:
        buffer.append(env)

    # since = ts of envelope 2 → strictly-after returns 3 and 4.
    drained = buffer.drain(since=envelopes[1].ts)
    assert [e.payload["n"] for e in drained] == [3, 4]
    # Envelopes 1 and 2 remain.
    assert [e.payload["n"] for e in buffer.snapshot()] == [1, 2]


def test_peek_does_not_empty() -> None:
    buffer = UIEventBuffer(maxlen=10)
    envelopes = [_envelope(i) for i in range(1, 4)]
    for env in envelopes:
        buffer.append(env)

    peeked = buffer.peek(since=None)
    assert [e.payload["n"] for e in peeked] == [1, 2, 3]
    # Unlike drain, peek leaves everything in place.
    assert [e.payload["n"] for e in buffer.snapshot()] == [1, 2, 3]


def test_on_append_fires_once_per_append_with_envelope() -> None:
    buffer = UIEventBuffer(maxlen=10)
    seen: list[UIEventEnvelope] = []
    buffer.on_append(seen.append)

    e1, e2 = _envelope(1), _envelope(2)
    buffer.append(e1)
    buffer.append(e2)

    assert seen == [e1, e2]


def test_concurrent_appends_do_not_interleave_or_lose_envelopes() -> None:
    buffer = UIEventBuffer(maxlen=20)
    group_a = [_envelope(i) for i in range(1, 6)]
    group_b = [_envelope(i) for i in range(11, 16)]

    async def _append_all(envelopes: list[UIEventEnvelope]) -> None:
        for env in envelopes:
            buffer.append(env)
            await asyncio.sleep(0)  # yield so the two tasks interleave at the loop level

    async def _run() -> None:
        await asyncio.gather(_append_all(group_a), _append_all(group_b))

    asyncio.run(_run())

    snap = buffer.snapshot()
    # No envelopes lost.
    assert len(snap) == 10
    assert {e.event_id for e in snap} == {e.event_id for e in (*group_a, *group_b)}
    # Each group's relative order is preserved (a single append is atomic — the
    # buffer never tears one task's envelope across another's).
    a_order = [e.payload["n"] for e in snap if e.payload["n"] < 10]
    b_order = [e.payload["n"] for e in snap if e.payload["n"] >= 10]
    assert a_order == [1, 2, 3, 4, 5]
    assert b_order == [11, 12, 13, 14, 15]
