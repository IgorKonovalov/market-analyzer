"""`POST /scan_chart_patterns` — the renderer's chart-pattern (trendline) sweep
trigger (Plan 0064 phase 3, ADR-0059).

Sibling of `POST /scan_patterns`: where that sweeps candlestick *markers*, this
sweeps classical chart *patterns* (H&S / doubles / triangles / wedges) over the
chart's current visible range and publishes their trendlines on the dedicated
`chart.trendlines` event, which arrives in the viewer over the existing
`/events` SSE stream — never in this response body. The synchronous reply is a
small ack (`{published, count}`); the trendlines themselves ride the bus.

Both this route and the `detect_chart_patterns` MCP tool run the SAME factored
core, `_detect_chart_patterns_response`, over bars fetched the SAME way, so the
agent path and the UI recompute path (Plan 0064 phase 5) emit identical geometry
and cannot drift. A sweep is derived and NOT persisted.

Renderer-bearer-gated by the central middleware in `app.py`, matching
`/scan_patterns`; a request carrying the MCP secret is rejected cross-tenant.
Error mapping mirrors `scan_patterns.py`: an unknown symbol → 404, a throttle →
429, any upstream/adapter failure → 502, a bad-input `ValueError` (unsupported
timeframe, reversed range, unknown pattern/state) → 422 — never a bare 500. An
empty/uncached range is not an error: it publishes nothing and returns
`{published: false, count: 0}`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.api.mcp_tools._shared.chart_patterns_response import (
    _detect_chart_patterns_response,
)
from market_analyser.data._http import ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.events import EventBus

router = APIRouter()


class ScanChartPatternsRequest(BaseModel):
    """`POST /scan_chart_patterns` body: the chart's current visible range for one
    symbol/timeframe, plus the optional pattern/state filters (same as the MCP
    tool)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    range_start: datetime
    range_end: datetime
    patterns: list[str] | None = None
    states: list[str] | None = None


class ScanChartPatternsResponse(BaseModel):
    """Synchronous ack for `POST /scan_chart_patterns`. The trendlines themselves
    arrive on the `/events` SSE stream, not here — `published` says whether a
    `chart.trendlines` event was emitted, `count` how many pattern hits it drew
    (0 ⇒ nothing in range)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    published: bool
    count: int


@router.post("/scan_chart_patterns", response_model=ScanChartPatternsResponse)
async def post_scan_chart_patterns(
    request: Request, body: ScanChartPatternsRequest
) -> ScanChartPatternsResponse:
    provider: MarketDataProvider = request.app.state.provider
    event_bus: EventBus = request.app.state.event_bus
    try:
        result = await _detect_chart_patterns_response(
            provider=provider,
            event_bus=event_bus,
            symbol=body.symbol,
            timeframe=body.timeframe,
            range_start=body.range_start,
            range_end=body.range_end,
            patterns=body.patterns,
            states=body.states,
        )
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RateLimitedError as exc:
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        )
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except (UpstreamUnavailableError, UpstreamDataError, ResilientHttpError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ScanChartPatternsResponse(
        published=bool(result["event_published"]), count=int(result["count"])
    )
