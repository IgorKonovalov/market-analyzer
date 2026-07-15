"""Watch-management MCP tools — Plan 0060 phase 3 (ADR-0055).

Four tools over the phase-1 repositories: `create_watch`, `list_watches`,
`delete_watch`, and the ADR-0046-paged `list_alerts`. The agent creates and
manages watches; the in-sidecar scheduler (started in the app lifespan) does
the evaluating and firing — these tools never evaluate anything themselves.

`create_watch` is the deep boundary: beyond the repository's own
kind/params/timeframe validation, a ``strategy_signal`` watch resolves its
strategy up front (unknown id, unsupported timeframe, or params the
strategy's own `Params` model rejects all fail *here*, at creation) so a
watch that could never evaluate is refused rather than parked to error on
every tick.

Alerts are condition reports, never recommendations (ADR-0029) — the payloads
these tools return are the stored `alert.triggered v1` facts.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.alerts.types import (
    INDICATOR_IDS,
    NOTE_MAX_LENGTH,
    PATTERN_NAMES,
    Alert,
    StrategySignalParams,
    Watch,
    validate_watch_params,
)
from market_analyser.api.mcp_tools._validation import _require_non_empty_symbol
from market_analyser.contracts.strategy import discover
from market_analyser.data.timeframes import supported_timeframes_label, timeframe_spec
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

# Maximum alerts returned inline in one page (ADR-0046). A serialized alert is
# ~250 chars (payload carries the condition string + a small values map), so a
# worst-case full page is ~50k chars — the same budget the other paged tools
# target, comfortably under the harness per-result cap.
MAX_ALERTS = 200

CREATE_WATCH_DESCRIPTION = (
    "Create a persisted watch the sidecar's alerting scheduler evaluates on an "
    "interval (ADR-0055). Three kinds: 'indicator_threshold' (params: "
    "{indicator, operator, level} with operator one of < <= > >= and indicator "
    f"one of {', '.join(sorted(INDICATOR_IDS))}), 'pattern' (params: {{pattern}} "
    f"one of {', '.join(sorted(PATTERN_NAMES))}), and 'strategy_signal' (params: "
    "{strategy_id, params} — fires when the strategy emits a fresh signal on "
    "the latest closed bar). Alerts are EDGE-TRIGGERED: one alert per "
    "false->true transition of the condition, evaluated on closed bars only. "
    "interval_seconds defaults to the timeframe's bar period. Alerts are "
    "condition facts, never buy/sell advice. Delivery: `alert.triggered v1` "
    "SSE event (viewer toast) + the pending-events poll + `list_alerts` "
    f"history. Supported timeframes: {supported_timeframes_label()}. Optional "
    f"`note` (<= {NOTE_MAX_LENGTH} chars): free-text context for WHY the watch "
    "exists (e.g. 'ETH long scenario A - neckline retest'), shown in the "
    "viewer's watch list and editable there."
)

LIST_WATCHES_DESCRIPTION = (
    "List the persisted watches (id, symbol, timeframe, kind, params, "
    "interval_seconds, enabled, last_state, created_at), ordered by id. "
    "`enabled_only=true` filters to the watches the scheduler is ticking."
)

DELETE_WATCH_DESCRIPTION = (
    "Delete a watch by id, including its alert history. Returns "
    "{deleted: bool} — false when the id does not exist (idempotent)."
)

LIST_ALERTS_DESCRIPTION = (
    "Read fired-alert history, newest first, optionally scoped to one "
    "watch_id. Each alert's payload is the condition-only `alert.triggered "
    "v1` fact (what condition, what values, when) — never a recommendation. "
    f"The inline result is bounded to {MAX_ALERTS} alerts per page: when more "
    "match, partial_reason='too_large' and total_available/offset/returned "
    "tell you how to page (call again with offset=offset+returned)."
)


class DeleteWatchResponse(BaseModel):
    """`delete_watch` outcome. `deleted=False` means the id did not exist."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: bool


class ListAlertsResponse(BaseModel):
    """One newest-first page of alert history plus the honest paging envelope
    (ADR-0046)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: list[Alert]
    partial_reason: Literal["too_large"] | None
    message: str | None
    total_available: int
    offset: int
    returned: int


def _validate_strategy_watch(timeframe: str, params: StrategySignalParams) -> None:
    """Resolve the strategy and validate the watch against it — unknown ids,
    unsupported timeframes, and bad strategy params are refused at creation
    (a watch that can never evaluate must not be parked to fail every tick)."""
    strategies = discover()
    if params.strategy_id not in strategies:
        raise ValueError(
            f"unknown strategy_id {params.strategy_id!r}; known: {sorted(strategies)}",
        )
    strategy_module = strategies[params.strategy_id]
    supported = strategy_module.META.timeframes
    if timeframe not in supported:
        raise ValueError(
            f"timeframe {timeframe!r} not supported by strategy "
            f"{params.strategy_id!r} (supported: {list(supported)})",
        )
    # Raises pydantic.ValidationError on violation — surfaced as a tool error.
    strategy_module.Params(**params.params)


def _create_watch_response(
    *,
    watches_repository: WatchesRepository,
    symbol: str,
    timeframe: str,
    kind: str,
    params: dict[str, Any],
    interval_seconds: int | None,
    enabled: bool,
    note: str | None = None,
    now: datetime | None = None,
) -> Watch:
    """Body of `create_watch`. `now` is injectable for tests; production reads
    the wall clock here (the tool boundary owns provenance timestamps — the
    repository and evaluators stay clock-free)."""
    _require_non_empty_symbol(symbol)
    params_model = validate_watch_params(kind, params)
    if isinstance(params_model, StrategySignalParams):
        _validate_strategy_watch(timeframe, params_model)
    effective_interval = (
        interval_seconds
        if interval_seconds is not None
        else int(timeframe_spec(timeframe).bar_duration.total_seconds())
    )
    resolved_now = now if now is not None else datetime.now(UTC)
    return watches_repository.create(
        symbol=symbol,
        timeframe=timeframe,
        kind=kind,
        params=params,
        interval_seconds=effective_interval,
        enabled=enabled,
        created_at=resolved_now,
        note=note,
    )


def _list_alerts_response(
    *,
    alerts_repository: AlertsRepository,
    watch_id: int | None,
    offset: int,
    max_alerts: int | None,
) -> ListAlertsResponse:
    """Body of `list_alerts`, factored out so the paging contract is
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

    return ListAlertsResponse(
        alerts=page,
        partial_reason=reason,
        message=message,
        total_available=total,
        offset=offset,
        returned=returned,
    )


def register_watch_tools(
    server: FastMCP,
    *,
    watches_repository: WatchesRepository,
    alerts_repository: AlertsRepository,
) -> None:
    """Bind the four watch tools to `server`. Repositories are captured by
    closure so each tool body keeps the declared parameter list FastMCP
    introspects to build the (strict) input schema."""

    @server.tool(description=CREATE_WATCH_DESCRIPTION)
    async def create_watch(
        symbol: str,
        timeframe: str,
        kind: str,
        params: dict[str, Any],
        interval_seconds: int | None = None,
        enabled: bool = True,
        note: Annotated[str, Field(max_length=NOTE_MAX_LENGTH)] | None = None,
    ) -> Watch:
        return await asyncio.to_thread(
            lambda: _create_watch_response(
                watches_repository=watches_repository,
                symbol=symbol,
                timeframe=timeframe,
                kind=kind,
                params=params,
                interval_seconds=interval_seconds,
                enabled=enabled,
                note=note,
            )
        )

    @server.tool(description=LIST_WATCHES_DESCRIPTION)
    async def list_watches(enabled_only: bool = False) -> list[Watch]:
        return await asyncio.to_thread(watches_repository.list, enabled_only=enabled_only)

    @server.tool(description=DELETE_WATCH_DESCRIPTION)
    async def delete_watch(watch_id: int) -> DeleteWatchResponse:
        deleted = await asyncio.to_thread(watches_repository.delete, watch_id)
        return DeleteWatchResponse(deleted=deleted)

    @server.tool(description=LIST_ALERTS_DESCRIPTION)
    async def list_alerts(
        watch_id: int | None = None,
        offset: int = 0,
        max_alerts: int | None = None,
    ) -> ListAlertsResponse:
        return await asyncio.to_thread(
            lambda: _list_alerts_response(
                alerts_repository=alerts_repository,
                watch_id=watch_id,
                offset=offset,
                max_alerts=max_alerts,
            )
        )


__all__ = [
    "CREATE_WATCH_DESCRIPTION",
    "DELETE_WATCH_DESCRIPTION",
    "LIST_ALERTS_DESCRIPTION",
    "LIST_WATCHES_DESCRIPTION",
    "MAX_ALERTS",
    "DeleteWatchResponse",
    "ListAlertsResponse",
    "_create_watch_response",
    "_list_alerts_response",
    "register_watch_tools",
]
