"""`POST /scan_patterns` — the renderer's pattern-sweep trigger (Plan 0049).

The UI twin of the `scan_patterns` MCP tool: the "Scan patterns" button posts the
chart's current visible range here, the route sweeps it, and the resulting markers
arrive in the viewer over the existing `/events` SSE stream — never in this
response body (no second draw path). The synchronous reply is a small ack
(`{published, count}`); the markers themselves ride the bus.

Both triggers run the SAME pure core, `analysis.markers.markers_for_range`, over
bars fetched the SAME way, so the agent path and the UI path emit byte-identical
markers and cannot drift (ADR-0045). Like the MCP tool, a sweep is derived and
NOT persisted: no annotation row is written.

Renderer-bearer-gated by the central middleware in `app.py`; a request carrying
the MCP secret is rejected cross-tenant (the agent uses the MCP tool instead).
Error mapping mirrors `routes/quote.py`/`routes/ohlcv.py`: an unknown symbol →
404, a throttle → 429, any upstream/adapter failure → 502, a bad-input
`ValueError` (unsupported timeframe, reversed range) → 422 — never a bare 500. An
empty/uncached range is not an error: it publishes nothing and returns
`{published: false, count: 0}`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.analysis.markers import markers_for_range
from market_analyser.annotations.types import SUPPORTED_TIMEFRAMES
from market_analyser.data._http import ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.events import ChartHighlightPayloadV1, EventBus

router = APIRouter()


class ScanPatternsRequest(BaseModel):
    """`POST /scan_patterns` body: the chart's current visible range for one
    symbol/timeframe, plus the optional sweep filters (same as the MCP tool)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    range_start: datetime
    range_end: datetime
    patterns: list[str] | None = None
    min_strength: float | None = None


class ScanPatternsResponse(BaseModel):
    """Synchronous ack for `POST /scan_patterns`. The markers themselves arrive on
    the `/events` SSE stream, not here — `published` says whether an event was
    emitted, `count` how many markers it carried (0 ⇒ nothing in range)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    published: bool
    count: int


@router.post("/scan_patterns", response_model=ScanPatternsResponse)
async def post_scan_patterns(request: Request, body: ScanPatternsRequest) -> ScanPatternsResponse:
    if body.timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe {body.timeframe!r} not supported "
            f"(supported: {sorted(SUPPORTED_TIMEFRAMES)})",
        )
    if body.range_end < body.range_start:
        raise HTTPException(
            status_code=422,
            detail=f"range_end {body.range_end.isoformat()} must be >= "
            f"range_start {body.range_start.isoformat()}",
        )

    provider: MarketDataProvider = request.app.state.provider
    event_bus: EventBus = request.app.state.event_bus
    try:
        bars = await asyncio.to_thread(
            provider.get_ohlcv, body.symbol, body.timeframe, body.range_start, body.range_end, None
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

    markers = await asyncio.to_thread(
        markers_for_range, list(bars), patterns=body.patterns, min_strength=body.min_strength
    )
    if not markers:
        return ScanPatternsResponse(published=False, count=0)
    payload = ChartHighlightPayloadV1(symbol=body.symbol, timeframe=body.timeframe, markers=markers)
    event_bus.publish("chart.highlight", payload)
    return ScanPatternsResponse(published=True, count=len(markers))
