"""`GET /agent_mode` + `PUT /agent_mode` — the agent-mode toggle (Plan 0014 phase 1).

Both routes are renderer-bearer-gated by the central middleware in `app.py`
(they live outside the `/mcp` prefix, so the MCP bearer 401s — an agent cannot
flip its own consent gate; the phase-1 done-when asserts this).

`PUT` persists the new state via the `AgentModeStore` and synthesises a
`ui.agent_mode_toggled v1` envelope into the buffer, so an agent watching the
MCP resource sees the user opt in or out (ADR-0021).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from market_analyser.api.ui_events import build_ui_event_envelope
from market_analyser.api.ui_events.agent_mode import AgentModeStore
from market_analyser.api.ui_events.buffer import UIEventBuffer

router = APIRouter(tags=["agent-mode"])


class AgentModeState(BaseModel):
    """The toggle's wire shape, both directions (`GET` response / `PUT` body).

    `StrictBool` so a `PUT` body like `{"enabled": "yes"}` is a 422 rather than
    being lax-coerced to `True` — the boundary rejects wrong types outright."""

    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


@router.get("/agent_mode", response_model=AgentModeState)
def get_agent_mode(request: Request) -> AgentModeState:
    store: AgentModeStore = request.app.state.agent_mode_store
    return AgentModeState(enabled=store.is_enabled())


@router.put("/agent_mode", response_model=AgentModeState)
def put_agent_mode(request: Request, body: AgentModeState) -> AgentModeState:
    store: AgentModeStore = request.app.state.agent_mode_store
    buffer: UIEventBuffer = request.app.state.ui_event_buffer
    store.set_enabled(body.enabled)
    # Synthesise the toggle event so an agent watching the buffer sees the
    # consent change. This event is buffered regardless of the new state — a
    # flip to OFF is itself something the agent may want to observe.
    buffer.append(
        build_ui_event_envelope("ui.agent_mode_toggled", {"enabled": body.enabled}),
    )
    return AgentModeState(enabled=store.is_enabled())
