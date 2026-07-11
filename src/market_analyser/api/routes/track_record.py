"""`GET /track_record` — Plan 0080 phase 5 prerequisite (ADR-0075, ADR-0046).

The renderer-facing read surface over the advisor's live track record. It is the
REST twin of the `get_track_record` MCP tool (the agent's surface): the renderer
cannot reach MCP tools, so — as with backtests (`get_backtest` tool + `/backtests`
route) — the same aggregate gets a REST route the typed fetch client can call.

Gated by the renderer bearer via the central middleware (`api.app.create_app`);
the MCP bearer must not authenticate it (the cross-tenant isolation rule). Mounted
only when the advice ledger exists (persistence wired), so the repository on
`app.state` is always present here.

The whole body — reading scored rows, running the honest aggregation, bounding the
recent-calls page — is the tool's factored-out `_get_track_record_response`,
reused verbatim so the two surfaces can never disagree.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from market_analyser.api.mcp_tools.track_record import (
    GetTrackRecordResponse,
    _get_track_record_response,
)
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerRepository

router = APIRouter()


@router.get("/track_record", response_model=GetTrackRecordResponse)
def get_track_record(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1),
    offset: int = Query(default=0, ge=0),
    max_calls: int | None = Query(default=None, ge=1),
) -> GetTrackRecordResponse:
    repo: AdviceLedgerRepository = request.app.state.advice_ledger_repository
    try:
        return _get_track_record_response(
            repository=repo,
            symbol=symbol,
            offset=offset,
            max_calls=max_calls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
