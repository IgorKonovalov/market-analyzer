"""Neutral UI-event buffer core (Plan 0072 phase 1, ADR-0065).

The renderer→agent feedback buffer ([ADR-0021]) is a shared primitive: domain
loops **produce** events (the in-sidecar watch scheduler today; DeFi jobs and
backfill tomorrow) and the `api` layer **consumes** them for the agent's poll.
Placing it here — a neutral top-level module, a sibling to `events/` — keeps the
dependency graph pointing one way (domain → core, api → core), so a background
loop can append an agent-pollable event without importing the web layer. This is
the same neutral-core placement [ADR-0032] gave the SSE event bus.

This package owns the transport-agnostic pieces:

- `UIEventEnvelope` — the buffered envelope shape (mirrors ADR-0017's
  `{type, version, ts, payload}`, plus a server-generated `event_id` for dedup).
- `buffer.UIEventBuffer` — the bounded in-memory ring buffer.

The transport-specific pieces stay under `api/`: the `POST /ui_events` route and
its closed `ui.*` vocabulary (`build_ui_event_envelope`, the payload registry),
and the persisted agent-mode toggle (`agent_mode.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UIEventEnvelope(BaseModel):
    """Buffered envelope. Mirrors ADR-0017's shape, adds `event_id` for dedup."""

    model_config = ConfigDict(frozen=True)

    event_id: str  # server-generated UUID v4
    type: str
    version: int
    ts: datetime  # server-generated at POST time
    payload: dict[str, Any]


__all__ = ["UIEventEnvelope"]
