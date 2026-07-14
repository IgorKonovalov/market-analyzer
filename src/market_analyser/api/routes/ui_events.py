"""`POST /ui_events` — buffer a renderer UI gesture for the agent (Plan 0014 phase 1).

Renderer-bearer-gated by the central middleware (an agent on `/mcp` cannot POST
here — the cross-tenant guarantee from ADR-0014). Forwarding is unconditional:
ADR-0101 removed the agent-mode gate, so the bearer is the only precondition.

The renderer sends `{type, version, payload}`; the server generates `event_id`
and `ts` and derives the authoritative `version` from the registry. An unknown
type or a payload that fails its per-type model is rejected at the boundary with
422 (closed type set + per-type Pydantic validation).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from market_analyser.api.ui_events import (
    UnknownUIEventTypeError,
    build_ui_event_envelope,
)
from market_analyser.ui_events.buffer import UIEventBuffer

router = APIRouter(tags=["ui-events"])


class UIEventRequest(BaseModel):
    """The renderer's POST body. `event_id` and `ts` are NOT accepted — the
    server stamps both. `version` is accepted for wire symmetry but the
    authoritative version is taken from the registry, not the request."""

    type: str
    version: int
    payload: dict[str, Any]


@router.post("/ui_events", status_code=202)
def post_ui_event(request: Request, body: UIEventRequest) -> dict[str, str]:
    buffer: UIEventBuffer = request.app.state.ui_event_buffer
    try:
        envelope = build_ui_event_envelope(body.type, body.payload)
    except UnknownUIEventTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationError as exc:
        # str(exc), not exc.errors(): a custom model_validator's ValueError is
        # carried in the error `ctx` and is not JSON-serializable in the detail.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    buffer.append(envelope)
    return {"event_id": envelope.event_id}
