"""UI-event vocabulary + envelope builder (Plan 0014 phase 1, ADR-0021).

The transport half of the renderer→agent feedback loop: the closed `ui.*`
payload vocabulary and `build_ui_event_envelope`, the boundary validator the
`POST /ui_events` route uses. The neutral envelope shape and the in-memory
buffer live in the top-level `market_analyser.ui_events` core (moved there in
Plan 0072 phase 1, ADR-0065) so domain producers need not import the api layer;
`UIEventEnvelope` is imported from there and re-exported here for the transition.

The renderer sends three typed UI-event payloads from the Electron viewer;
`build_ui_event_envelope` validates `(type, payload)` and wraps the result in a
`UIEventEnvelope`, gated at the route by the agent-mode toggle
(`agent_mode.AgentModeStore`). The agent reads them back via the MCP surface.

The envelope adds a server-generated `event_id` so the agent can dedupe across
the draining tool read and the non-draining resource read (the open question
ADR-0021 flagged). `ts` and `event_id` are both server-generated at POST time —
the renderer supplies neither.

The type set is *closed*: `build_ui_event_envelope` rejects an unknown type with
`UnknownUIEventTypeError` so the `POST /ui_events` boundary can return 422 rather
than buffering an event the agent's vocabulary doesn't include.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from market_analyser.ui_events import UIEventEnvelope


class RangeSelectedPayloadV1(BaseModel):
    """`ui.range_selected v1`: the user drag-selected a [start, end] window."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime

    @model_validator(mode="after")
    def _ordered_range(self) -> RangeSelectedPayloadV1:
        if self.range_end < self.range_start:
            raise ValueError(
                f"range_end {self.range_end.isoformat()} must be >= "
                f"range_start {self.range_start.isoformat()}",
            )
        return self


class BarClickedPayloadV1(BaseModel):
    """`ui.bar_clicked v1`: the user clicked a single candle."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    event_ts: datetime
    open: float
    high: float
    low: float
    close: float


class AgentModeToggledPayloadV1(BaseModel):
    """`ui.agent_mode_toggled v1`: the toggle flipped (synthesised by PUT /agent_mode)."""

    VERSION: ClassVar[int] = 1
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool


UI_EVENT_TYPE_REGISTRY: dict[str, type[BaseModel]] = {
    "ui.range_selected": RangeSelectedPayloadV1,
    "ui.bar_clicked": BarClickedPayloadV1,
    "ui.agent_mode_toggled": AgentModeToggledPayloadV1,
}


class UnknownUIEventTypeError(ValueError):
    """`build_ui_event_envelope` was called with a type outside the closed set."""


def build_ui_event_envelope(event_type: str, raw_payload: dict[str, Any]) -> UIEventEnvelope:
    """Validate `(event_type, raw_payload)` and build a server-stamped envelope.

    Raises `UnknownUIEventTypeError` for a type outside `UI_EVENT_TYPE_REGISTRY`
    and `pydantic.ValidationError` for a payload that fails its per-type model.
    The `POST /ui_events` route maps both to 422 — the closed type set and the
    per-type model are the boundary validation (ADR-0021).

    `event_id` (UUID v4) and `ts` (UTC now) are generated here, never supplied by
    the renderer. `version` comes from the registered model, not the request, so
    a renderer claiming the wrong version cannot mislabel the envelope.
    """
    model = UI_EVENT_TYPE_REGISTRY.get(event_type)
    if model is None:
        raise UnknownUIEventTypeError(
            f"unknown UI event type: {event_type!r} (supported: {sorted(UI_EVENT_TYPE_REGISTRY)})",
        )
    validated = model.model_validate(raw_payload)
    # `VERSION` is a `ClassVar[int]` on every concrete payload model in the
    # registry; mypy can't see it on the abstract `type[BaseModel]` constraint.
    version: int = getattr(model, "VERSION")  # noqa: B009
    return UIEventEnvelope(
        event_id=str(uuid.uuid4()),
        type=event_type,
        version=version,
        ts=datetime.now(tz=UTC),
        payload=validated.model_dump(mode="json"),
    )
