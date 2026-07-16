"""Renderer-facing DeFi position-watch read routes (Plan 0099 phase 2 seam).

The viewer's Alerts surface (phase 4) needs two thin reads the MCP toolset
does not serve (that toolset is agent-side, behind the MCP bearer):

- ``GET /defi/position_watches`` — the watch list the view renders;
- ``GET /defi/position_alerts`` — newest-first alert history, offset/limit
  paged (SSE only carries live fires; history needs a read on view load).

Read-only by design: position watches are config-pinned or agent-created
(ADR-0093) — there is no viewer-side create/edit grain here (unlike the
market-watch routes, where Plan 0110 added management). Both routes are
renderer-bearer-gated by the central middleware in `app.py` and registered
only when the position-watch repositories exist (persistence wired). Pure
repository pass-throughs — the dwell logic stays in `defi/`.

`PositionWatchOut` / `PositionAlertOut` / `PositionAlertsPage` are named
`response_model`s so `gen-types.mjs` emits them into
`renderer/types/sidecar/`. The wallet is masked in both — the renderer
surface never needs the full address (the alerts view shows pool + range
facts), and full addresses stay out of renderer state by default.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from market_analyser.defi.discovery import mask_wallet
from market_analyser.defi.position_watch import DefiPositionAlert, DefiPositionWatch
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)

router = APIRouter()

# One page of the renderer's alert history. Bounded like the MCP tool's page
# (ADR-0046 discipline), though the view typically asks for far less.
MAX_PAGE_LIMIT = 100
DEFAULT_PAGE_LIMIT = 50


class PositionWatchOut(BaseModel):
    """Renderer envelope for one position-watch definition (wallet masked)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    wallet: str
    chain: str
    pool_address: str
    nft_token_id: int | None
    dwell_hours: float
    interval_seconds: int
    enabled: bool
    source: str
    created_at: datetime
    out_since: datetime | None
    alert_fired: bool

    @classmethod
    def from_watch(cls, watch: DefiPositionWatch) -> PositionWatchOut:
        return cls(
            id=watch.id,
            wallet=mask_wallet(watch.wallet),
            chain=watch.chain,
            pool_address=watch.pool_address,
            nft_token_id=watch.nft_token_id,
            dwell_hours=watch.dwell_hours,
            interval_seconds=watch.interval_seconds,
            enabled=watch.enabled,
            source=watch.source,
            created_at=watch.created_at,
            out_since=watch.dwell_state.out_since,
            alert_fired=watch.dwell_state.fired,
        )


class PositionAlertOut(BaseModel):
    """Renderer envelope for one fired position alert — the condition-only
    out-of-range fact (ADR-0029: never advice). Wallet masked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    watch_id: int
    wallet: str
    chain: str
    pool_address: str
    nft_token_id: int | None
    fired_at: datetime
    out_since: datetime
    hours_out: float
    tick_lower: int
    tick_upper: int
    current_tick: int

    @classmethod
    def from_alert(cls, alert: DefiPositionAlert) -> PositionAlertOut:
        return cls(
            id=alert.id,
            watch_id=alert.watch_id,
            wallet=mask_wallet(alert.wallet),
            chain=alert.chain,
            pool_address=alert.pool_address,
            nft_token_id=alert.nft_token_id,
            fired_at=alert.fired_at,
            out_since=alert.out_since,
            hours_out=alert.hours_out,
            tick_lower=alert.tick_lower,
            tick_upper=alert.tick_upper,
            current_tick=alert.current_tick,
        )


class PositionAlertsPage(BaseModel):
    """One newest-first page of position-alert history plus the total count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: list[PositionAlertOut]
    total: int


def _watches_repository(request: Request) -> DefiPositionWatchesRepository:
    repo: DefiPositionWatchesRepository | None = request.app.state.position_watches_repository
    if repo is None:  # pragma: no cover — router is only mounted when wired
        raise HTTPException(status_code=503, detail="position-watch persistence not configured")
    return repo


def _alerts_repository(request: Request) -> DefiPositionAlertsRepository:
    repo: DefiPositionAlertsRepository | None = request.app.state.position_alerts_repository
    if repo is None:  # pragma: no cover — router is only mounted when wired
        raise HTTPException(status_code=503, detail="position-watch persistence not configured")
    return repo


@router.get("/defi/position_watches", response_model=list[PositionWatchOut])
def list_position_watches(request: Request) -> list[PositionWatchOut]:
    return [PositionWatchOut.from_watch(w) for w in _watches_repository(request).list()]


@router.get("/defi/position_alerts", response_model=PositionAlertsPage)
def list_position_alerts(
    request: Request,
    watch_id: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
) -> PositionAlertsPage:
    page, total = _alerts_repository(request).list(watch_id=watch_id, offset=offset, limit=limit)
    return PositionAlertsPage(alerts=[PositionAlertOut.from_alert(a) for a in page], total=total)
