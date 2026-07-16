"""`recommend_rebalance` MCP tool — Plan 0099 phase 3 (ADR-0029/0093).

The recommend-family sibling for DeFi LP positions: resolve one position's
health context from the position-watch subsystem (a fired out-of-range
alert, qualified by the watch's *current* dwell state) and run the pure
advisor fusion (`advisor/rebalance.py`) into a single labeled advisory
`RebalanceRecommendation` — recenter / widen / exit, or an honest hold.

A **sibling entrypoint**, not a `recommend` mode: the fused trade tool's
input grain (symbol / timeframe / strategy / bars) and the rebalance grain
(watch / alert / on-chain ticks) share no parameters, so folding them into
one discriminated schema would loosen every required field of a shipped
tool (the `technical_read` precedent — a distinct advisory entrypoint
beside `recommend`, same family, same ADR-0029 label discipline).

Context resolution favours honesty over drama: the stored alert supplies
the out-of-range facts, but the watch's **current** dwell state wins — a
position that re-entered its range since the alert yields "hold / no
action", never a stale rebalance call.

**Advisory only, structurally** (ADR-0029): the tool consumes no secret
store, opens no network path (not even the RPC — it reads the persisted
facts the monitor already recorded), and returns an artifact whose `label`
can only be `"advisory"`. On-chain rebalancing is ADR-0072 BA-1-barred and
ADR-0025-untaken; an AST test pins that no order/key/network path exists
in this module or the advisor package. Wallets are masked at this boundary
before they enter the recommendation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.advisor.rebalance import (
    LpPositionContext,
    RebalanceRecommendation,
    recommend_rebalance,
)
from market_analyser.defi.discovery import mask_wallet
from market_analyser.defi.position_watch import DefiPositionAlert, DefiPositionWatch
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)

RECOMMEND_REBALANCE_DESCRIPTION = (
    "ADVISORY ONLY - turn a DeFi LP out-of-range alert into a single labeled "
    "rebalance recommendation: recenter / widen / exit, or an honest 'hold'. "
    "Pass watch_id (uses that watch's newest alert, qualified by its CURRENT "
    "dwell state - a position that re-entered its range yields hold/no-action) "
    "or alert_id (scores that specific fired alert). The direction comes from "
    "a stated excursion-depth heuristic (how many range-widths price sits "
    "beyond the bound) and every recommendation carries its rationale and the "
    "numeric basis behind it (ADR-0029). A healthy in-range position yields "
    "'hold - no action'; missing on-chain detail yields 'hold - insufficient "
    "basis', never a guessed direction. This tool holds no trade key, builds "
    "no transaction, places no order, and moves no funds - on-chain "
    "rebalancing is out of scope by decision (ADR-0072 BA-1 / ADR-0025); the "
    "user decides and acts."
)


def _context_from_alert(
    alert: DefiPositionAlert, watch: DefiPositionWatch | None
) -> LpPositionContext:
    """Project a fired alert (+ its watch's dwell threshold) onto the fusion's
    input shape. The alert is an out-of-range fact by construction."""
    fees = (
        {token.symbol: token.amount for token in alert.uncollected_fees}
        if alert.uncollected_fees
        else None
    )
    return LpPositionContext(
        wallet=mask_wallet(alert.wallet),
        chain=alert.chain,
        pool_address=alert.pool_address,
        nft_token_id=alert.nft_token_id,
        in_range=False,
        tick_lower=alert.tick_lower,
        tick_upper=alert.tick_upper,
        current_tick=alert.current_tick,
        hours_out=alert.hours_out,
        dwell_hours=watch.dwell_hours if watch is not None else None,
        uncollected_fees=fees,
    )


def _in_range_context(watch: DefiPositionWatch) -> LpPositionContext:
    return LpPositionContext(
        wallet=mask_wallet(watch.wallet),
        chain=watch.chain,
        pool_address=watch.pool_address,
        nft_token_id=watch.nft_token_id,
        in_range=True,
    )


def _recommend_rebalance_response(
    *,
    watches_repository: DefiPositionWatchesRepository,
    alerts_repository: DefiPositionAlertsRepository,
    watch_id: int | None,
    alert_id: int | None,
    now: datetime | None = None,
) -> RebalanceRecommendation:
    """Body of `recommend_rebalance`. `now` is injectable for tests;
    production reads the wall clock here (the tool boundary owns the `as_of`
    provenance — the fusion stays clock-free)."""
    if watch_id is None and alert_id is None:
        raise ValueError("provide watch_id or alert_id")
    resolved_now = now if now is not None else datetime.now(UTC)

    if alert_id is not None:
        alert = alerts_repository.get(alert_id)
        if alert is None:
            raise ValueError(f"unknown alert_id {alert_id}")
        watch = watches_repository.get(alert.watch_id)
        return recommend_rebalance(_context_from_alert(alert, watch), as_of=resolved_now)

    assert watch_id is not None  # guarded above
    watch = watches_repository.get(watch_id)
    if watch is None:
        raise ValueError(f"unknown watch_id {watch_id}")

    # The watch's CURRENT dwell state is the freshest persisted fact and wins
    # over any stored alert: no excursion in progress means the position is
    # (as of the monitor's last evaluation) back in — or never left — range.
    if watch.dwell_state.out_since is None:
        return recommend_rebalance(_in_range_context(watch), as_of=resolved_now)

    page, _total = alerts_repository.list(watch_id=watch_id, offset=0, limit=1)
    if page:
        return recommend_rebalance(_context_from_alert(page[0], watch), as_of=resolved_now)

    # Out of range but no alert yet: the excursion has not met the dwell
    # threshold — an honest hold, not a premature direction (ADR-0093's
    # dwell qualifier is the whole point of the alert).
    hours_out = (resolved_now - watch.dwell_state.out_since).total_seconds() / 3600.0
    context = LpPositionContext(
        wallet=mask_wallet(watch.wallet),
        chain=watch.chain,
        pool_address=watch.pool_address,
        nft_token_id=watch.nft_token_id,
        in_range=False,
        hours_out=round(hours_out, 4),
        dwell_hours=watch.dwell_hours,
    )
    return recommend_rebalance(context, as_of=resolved_now)


def register_recommend_rebalance(
    server: FastMCP,
    *,
    position_watches_repository: DefiPositionWatchesRepository,
    position_alerts_repository: DefiPositionAlertsRepository,
) -> None:
    """Bind the `recommend_rebalance` tool to `server`. Repositories are
    captured by closure so the tool body keeps the declared parameter list
    FastMCP introspects to build the (strict) input schema."""

    @server.tool(description=RECOMMEND_REBALANCE_DESCRIPTION)
    async def recommend_rebalance(
        watch_id: int | None = None,
        alert_id: int | None = None,
    ) -> RebalanceRecommendation:
        return await asyncio.to_thread(
            lambda: _recommend_rebalance_response(
                watches_repository=position_watches_repository,
                alerts_repository=position_alerts_repository,
                watch_id=watch_id,
                alert_id=alert_id,
            )
        )


__all__ = [
    "RECOMMEND_REBALANCE_DESCRIPTION",
    "_recommend_rebalance_response",
    "register_recommend_rebalance",
]
