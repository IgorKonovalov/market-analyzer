"""Bounded in-memory ring buffer of UI events (Plan 0014 phase 1, ADR-0021).

A `collections.deque(maxlen=N)` of `UIEventEnvelope`s. Drop-oldest on overflow.
Empty whenever agent mode is OFF (the `POST /ui_events` route 403s before
appending) and on every fresh sidecar boot (no persistence — UI gestures are
ephemeral; ADR-0021 Alternative D rejects SQLite-backed history).

Read semantics distinguish *draining* from *peeking*:

- `drain(since)` returns and *removes* the matching envelopes — the reliable MCP
  tool's default. Consecutive drains return disjoint sets.
- `peek(since)` / `snapshot()` return without removing — the MCP resource read.

`since` is strict-greater-than on `ts`: `drain(since=t)` returns envelopes
stamped *after* `t` and leaves the rest in place. `None` means "everything".

The methods are synchronous and contain no `await` points, so on a single
asyncio loop two concurrent appends can't interleave (the loop only switches
tasks at an await). That satisfies the phase-1 concurrency done-when without an
explicit lock — `deque.append` is itself atomic under the GIL.

`on_append(callback)` registers a seam fired synchronously after every append;
phase 2 uses it to publish the `notifications/resources/updated` MCP
notification.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime

from market_analyser.api.ui_events import UIEventEnvelope

DEFAULT_MAXLEN = 100


class UIEventBuffer:
    """Drop-oldest ring buffer of UI-event envelopes with drain/peek reads."""

    def __init__(self, *, maxlen: int = DEFAULT_MAXLEN) -> None:
        self._events: deque[UIEventEnvelope] = deque(maxlen=maxlen)
        self._on_append: list[Callable[[UIEventEnvelope], None]] = []

    def append(self, envelope: UIEventEnvelope) -> None:
        """Append an envelope (drop-oldest on overflow), then fire `on_append`
        callbacks synchronously with the appended envelope."""
        self._events.append(envelope)
        for callback in self._on_append:
            callback(envelope)

    def snapshot(self) -> list[UIEventEnvelope]:
        """Return all buffered envelopes in append order, without removing any."""
        return list(self._events)

    def peek(self, since: datetime | None = None) -> list[UIEventEnvelope]:
        """Return envelopes with `ts > since` (or all when `since is None`)
        without removing them."""
        return [e for e in self._events if since is None or e.ts > since]

    def drain(self, since: datetime | None = None) -> list[UIEventEnvelope]:
        """Return envelopes with `ts > since` (or all when `since is None`) AND
        remove exactly those from the buffer; envelopes with `ts <= since` stay."""
        if since is None:
            drained = list(self._events)
            self._events.clear()
            return drained
        drained = [e for e in self._events if e.ts > since]
        retained = [e for e in self._events if e.ts <= since]
        self._events.clear()
        self._events.extend(retained)
        return drained

    def on_append(self, callback: Callable[[UIEventEnvelope], None]) -> None:
        """Register a callback fired once per `append`, with the appended envelope."""
        self._on_append.append(callback)
