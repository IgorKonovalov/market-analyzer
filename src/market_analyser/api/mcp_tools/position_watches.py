"""Position-watch MCP tools — Plan 0099 phase 2 (ADR-0093).

Four tools over the phase-1 repositories, mirroring the ADR-0055 watch
toolset grain: `create_position_watch`, `list_position_watches`,
`delete_position_watch`, and the ADR-0046-paged `list_position_alerts`. The
agent creates and manages watches; the in-sidecar position monitor (started
in the app lifespan) does the reading and firing — these tools never touch
the chain themselves.

`create_position_watch` is the boundary: address shapes, chain, dwell, and
interval are validated at creation (in the repository), so a watch that
could never be stored is refused rather than parked. Note that a watch on a
pool the RPC adapter cannot deep-read never fires — the monitor surfaces it
as "unreadable" in its `/healthz` heartbeat, distinctly from "in range".

Alerts are condition reports, never recommendations (ADR-0029) — the
persisted `DefiPositionAlert` facts these tools return carry ticks, dwell
hours, and forgone-fee context; no direction/advice field exists on the
model. Rebalance advice is the advisor layer's separate `recommend` surface
(phase 3).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.defi.position_watch import (
    DEFAULT_DWELL_HOURS,
    DEFAULT_INTERVAL_SECONDS,
    DefiPositionAlert,
    DefiPositionWatch,
)
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)

# Maximum alerts returned inline in one page (ADR-0046). A serialized position
# alert is ~450 chars (tick facts + fee context), so a worst-case full page is
# ~45k chars — the same budget the other paged tools target.
MAX_ALERTS = 100

CREATE_POSITION_WATCH_DESCRIPTION = (
    "Create a persisted watch over one concentrated-liquidity LP position the "
    "sidecar's DeFi position monitor re-reads on-chain on an interval "
    "(ADR-0093). Identify the position by wallet (0x address), chain "
    "(ethereum/base/arbitrum/optimism; deep reads need that chain's RPC URL "
    "secret), pool_address, and optionally nft_token_id (omit to match the "
    "wallet's CL position in the pool). The alert is DWELL-QUALIFIED: it fires "
    "exactly once after the position has been continuously out of its tick "
    "range for >= dwell_hours (default 6.0), then re-arms when price re-enters "
    "the range. A one-tick excursion never fires. Alerts are condition facts "
    "(ticks, hours out, uncollected fees) - never rebalance advice (use "
    "`recommend` for that). Delivery: `defi.position_alert v1` SSE event "
    "(viewer toast + OS notification) + the pending-events poll + "
    "`list_position_alerts` history. interval_seconds defaults to 900 (15 min "
    "- LP ranges move on the timescale of hours; each check is an RPC read)."
)

LIST_POSITION_WATCHES_DESCRIPTION = (
    "List the persisted DeFi position watches (id, wallet, chain, "
    "pool_address, nft_token_id, dwell_hours, interval_seconds, enabled, "
    "source config|agent, dwell_state), ordered by id. `enabled_only=true` "
    "filters to the watches the monitor is ticking. A watch whose pool the "
    "RPC adapter cannot deep-read never fires - the monitor's /healthz "
    "heartbeat surfaces it as 'unreadable'."
)

DELETE_POSITION_WATCH_DESCRIPTION = (
    "Delete a DeFi position watch by id, including its alert history. Returns "
    "{deleted: bool} - false when the id does not exist (idempotent)."
)

LIST_POSITION_ALERTS_DESCRIPTION = (
    "Read fired DeFi position-alert history, newest first, optionally scoped "
    "to one watch_id. Each alert is the condition-only out-of-range fact "
    "(pool, tick range vs current tick, hours out of range, uncollected fees "
    "at fire) - never a recommendation; ask `recommend` for the advisory "
    f"rebalance call. The inline result is bounded to {MAX_ALERTS} alerts per "
    "page: when more match, partial_reason='too_large' and "
    "total_available/offset/returned tell you how to page (call again with "
    "offset=offset+returned)."
)


class DeletePositionWatchResponse(BaseModel):
    """`delete_position_watch` outcome. `deleted=False` means the id did not
    exist."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: bool


class ListPositionAlertsResponse(BaseModel):
    """One newest-first page of position-alert history plus the honest paging
    envelope (ADR-0046)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: list[DefiPositionAlert]
    partial_reason: Literal["too_large"] | None
    message: str | None
    total_available: int
    offset: int
    returned: int


def _create_position_watch_response(
    *,
    watches_repository: DefiPositionWatchesRepository,
    wallet: str,
    chain: str,
    pool_address: str,
    nft_token_id: int | None,
    dwell_hours: float,
    interval_seconds: int,
    enabled: bool,
    now: datetime | None = None,
) -> DefiPositionWatch:
    """Body of `create_position_watch`. `now` is injectable for tests;
    production reads the wall clock here (the tool boundary owns provenance
    timestamps — the repository and reducer stay clock-free)."""
    resolved_now = now if now is not None else datetime.now(UTC)
    return watches_repository.create(
        wallet=wallet,
        chain=chain,
        pool_address=pool_address,
        nft_token_id=nft_token_id,
        dwell_hours=dwell_hours,
        interval_seconds=interval_seconds,
        enabled=enabled,
        source="agent",
        created_at=resolved_now,
    )


def _list_position_alerts_response(
    *,
    alerts_repository: DefiPositionAlertsRepository,
    watch_id: int | None,
    offset: int,
    max_alerts: int | None,
) -> ListPositionAlertsResponse:
    """Body of `list_position_alerts`, factored out so the paging contract is
    unit-testable without a live MCP server."""
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if max_alerts is not None and max_alerts < 1:
        raise ValueError(f"max_alerts must be >= 1, got {max_alerts}")
    page_size = MAX_ALERTS if max_alerts is None else min(max_alerts, MAX_ALERTS)
    page, total = alerts_repository.list(watch_id=watch_id, offset=offset, limit=page_size)
    returned = len(page)
    more_remain = offset + returned < total

    reason: Literal["too_large"] | None
    message: str | None
    if more_remain:
        reason = "too_large"
        message = (
            f"returned alerts[{offset}:{offset + returned}] of {total} total - more "
            f"remain; page on with offset={offset + returned} "
            f"(page size {page_size}, max {MAX_ALERTS})."
        )
    else:
        reason = None
        message = None

    return ListPositionAlertsResponse(
        alerts=page,
        partial_reason=reason,
        message=message,
        total_available=total,
        offset=offset,
        returned=returned,
    )


def register_position_watch_tools(
    server: FastMCP,
    *,
    position_watches_repository: DefiPositionWatchesRepository,
    position_alerts_repository: DefiPositionAlertsRepository,
) -> None:
    """Bind the four position-watch tools to `server`. Repositories are
    captured by closure so each tool body keeps the declared parameter list
    FastMCP introspects to build the (strict) input schema."""

    @server.tool(description=CREATE_POSITION_WATCH_DESCRIPTION)
    async def create_position_watch(
        wallet: str,
        chain: str,
        pool_address: str,
        nft_token_id: int | None = None,
        dwell_hours: float = DEFAULT_DWELL_HOURS,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
    ) -> DefiPositionWatch:
        return await asyncio.to_thread(
            lambda: _create_position_watch_response(
                watches_repository=position_watches_repository,
                wallet=wallet,
                chain=chain,
                pool_address=pool_address,
                nft_token_id=nft_token_id,
                dwell_hours=dwell_hours,
                interval_seconds=interval_seconds,
                enabled=enabled,
            )
        )

    @server.tool(description=LIST_POSITION_WATCHES_DESCRIPTION)
    async def list_position_watches(enabled_only: bool = False) -> list[DefiPositionWatch]:
        return await asyncio.to_thread(position_watches_repository.list, enabled_only=enabled_only)

    @server.tool(description=DELETE_POSITION_WATCH_DESCRIPTION)
    async def delete_position_watch(watch_id: int) -> DeletePositionWatchResponse:
        deleted = await asyncio.to_thread(position_watches_repository.delete, watch_id)
        return DeletePositionWatchResponse(deleted=deleted)

    @server.tool(description=LIST_POSITION_ALERTS_DESCRIPTION)
    async def list_position_alerts(
        watch_id: int | None = None,
        offset: int = 0,
        max_alerts: int | None = None,
    ) -> ListPositionAlertsResponse:
        return await asyncio.to_thread(
            lambda: _list_position_alerts_response(
                alerts_repository=position_alerts_repository,
                watch_id=watch_id,
                offset=offset,
                max_alerts=max_alerts,
            )
        )


__all__ = [
    "CREATE_POSITION_WATCH_DESCRIPTION",
    "DELETE_POSITION_WATCH_DESCRIPTION",
    "LIST_POSITION_ALERTS_DESCRIPTION",
    "LIST_POSITION_WATCHES_DESCRIPTION",
    "MAX_ALERTS",
    "DeletePositionWatchResponse",
    "ListPositionAlertsResponse",
    "_create_position_watch_response",
    "_list_position_alerts_response",
    "register_position_watch_tools",
]
