"""Renderer-facing watch + alert routes (Plan 0060 phase 4 seam).

The viewer's Alerts surface needs four thin reads/writes the MCP toolset
does not serve (that toolset is agent-side, behind the MCP bearer):

- ``GET /watches`` — the watch list the view renders;
- ``POST /watches/{watch_id}`` — partial update `{enabled?, note?}` (the
  "agent creates, viewer manages" grain, ADR-0015: creation stays MCP-only;
  Plan 0110 widened the managed fields beyond enable/disable);
- ``DELETE /watches/{watch_id}`` — delete with the same alert-history
  cascade as MCP `delete_watch`;
- ``GET /alerts`` — newest-first alert history, offset/limit paged.

All four are renderer-bearer-gated by the central middleware in `app.py` and
registered only when the alerting repositories exist (i.e. persistence is
wired). Pure repository pass-throughs — no evaluation, no scheduling; the
domain logic stays in `alerts/`.

`WatchOut` / `AlertOut` / `AlertsPage` are named `response_model`s so
`gen-types.mjs` emits them into `renderer/types/sidecar/`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_analyser.alerts.types import NOTE_MAX_LENGTH, Alert, Watch
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

router = APIRouter()

# One page of the renderer's alert history. Bounded like the MCP tool's page
# (ADR-0046 discipline), though the view typically asks for far less.
MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 50


class WatchOut(BaseModel):
    """Renderer envelope for one watch definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    symbol: str
    timeframe: str
    kind: str
    params: dict[str, Any]
    interval_seconds: int
    enabled: bool
    last_state: bool | None
    created_at: datetime
    note: str | None

    @classmethod
    def from_watch(cls, watch: Watch) -> WatchOut:
        return cls(
            id=watch.id,
            symbol=watch.symbol,
            timeframe=watch.timeframe,
            kind=watch.kind,
            params=watch.params.model_dump(mode="json"),
            interval_seconds=watch.interval_seconds,
            enabled=watch.enabled,
            last_state=watch.last_state,
            created_at=watch.created_at,
            note=watch.note,
        )


class AlertOut(BaseModel):
    """Renderer envelope for one fired alert. `payload` is the stored
    condition-only `alert.triggered v1` fact (ADR-0029: never advice)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    watch_id: int
    fired_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_alert(cls, alert: Alert) -> AlertOut:
        return cls(
            id=alert.id,
            watch_id=alert.watch_id,
            fired_at=alert.fired_at,
            payload=alert.payload,
        )


class AlertsPage(BaseModel):
    """One newest-first page of alert history plus the total match count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: list[AlertOut]
    total: int


class WatchUpdateRequest(BaseModel):
    """Body of `POST /watches/{watch_id}` — a partial update over the fields
    the viewer manages. Absent field = untouched; explicit `note: null`
    clears the note (distinguished via `model_fields_set`, so an
    enable/disable toggle never silently wipes a note)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> WatchUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one of 'enabled' or 'note' must be supplied")
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("'enabled' cannot be null (it is not clearable)")
        return self


class WatchDeleteResponse(BaseModel):
    """Body of a successful `DELETE /watches/{watch_id}` (unknown ids 404)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: bool


def _watches_repository(request: Request) -> WatchesRepository:
    repo: WatchesRepository | None = request.app.state.watches_repository
    if repo is None:  # pragma: no cover — router is only mounted when wired
        raise HTTPException(status_code=503, detail="alerting persistence not configured")
    return repo


def _alerts_repository(request: Request) -> AlertsRepository:
    repo: AlertsRepository | None = request.app.state.alerts_repository
    if repo is None:  # pragma: no cover — router is only mounted when wired
        raise HTTPException(status_code=503, detail="alerting persistence not configured")
    return repo


@router.get("/watches", response_model=list[WatchOut])
def list_watches(request: Request) -> list[WatchOut]:
    return [WatchOut.from_watch(w) for w in _watches_repository(request).list()]


@router.post("/watches/{watch_id}", response_model=WatchOut)
def update_watch(
    request: Request,
    watch_id: int,
    body: WatchUpdateRequest,
) -> WatchOut:
    repo = _watches_repository(request)
    if body.enabled is not None and not repo.set_enabled(watch_id, enabled=body.enabled):
        raise HTTPException(status_code=404, detail=f"unknown watch_id {watch_id}")
    if "note" in body.model_fields_set and not repo.set_note(watch_id, body.note):
        raise HTTPException(status_code=404, detail=f"unknown watch_id {watch_id}")
    watch = repo.get(watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail=f"unknown watch_id {watch_id}")
    return WatchOut.from_watch(watch)


@router.delete("/watches/{watch_id}", response_model=WatchDeleteResponse)
def delete_watch(request: Request, watch_id: int) -> WatchDeleteResponse:
    """Same cascade semantics as MCP `delete_watch`: the watch and its alert
    history rows go together (`WatchesRepository.delete`)."""
    if not _watches_repository(request).delete(watch_id):
        raise HTTPException(status_code=404, detail=f"unknown watch_id {watch_id}")
    return WatchDeleteResponse(deleted=True)


@router.get("/alerts", response_model=AlertsPage)
def list_alerts(
    request: Request,
    watch_id: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
) -> AlertsPage:
    page, total = _alerts_repository(request).list(watch_id=watch_id, offset=offset, limit=limit)
    return AlertsPage(alerts=[AlertOut.from_alert(a) for a in page], total=total)
