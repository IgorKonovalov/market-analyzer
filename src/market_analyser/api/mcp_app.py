"""FastMCP server definition + tool registration (ADR-0014, Plan 0006 phase 4).

Three production tools exposed to MCP clients (Claude Desktop, etc):

- `get_ohlcv` — read cached OHLCV bars through the renderer-side
  MarketDataProvider. `as_of` is fixed to `None` (live mode) at this
  boundary; a backtest-aware variant would ship as a separate tool, not
  as an exposed parameter, so the anti-lookahead guarantee from ADR-0007
  is preserved at the MCP seam.
- `write_annotation` — persist an agent-written chart marker. Returns
  the populated `Annotation` (with `id` and `created_at` filled in by
  the model's default factories) so the agent can reference its writes.
- `list_annotations` — read annotations for a symbol/timeframe window.
  Same boundary-inclusive semantics as the renderer's GET /annotations.

Dependencies (provider + annotations repository) are bound by closure
when the components factory runs. Tests inject fakes; production passes
the live provider and repo built from the SQLite engine.

The MCP transport is Streamable HTTP at exactly `/mcp` (no trailing-
slash redirect). We deliberately bypass `FastMCP.streamable_http_app()`'s
outer Starlette wrapper because mounting that on FastAPI under `/mcp`
issues a 307 redirect from `/mcp` → `/mcp/` (Starlette's Mount semantics
for empty-suffix paths), which trips simple MCP clients that don't
follow redirects for POST. Instead we surface the inner
`StreamableHTTPASGIApp` and register it as a single ASGI route on the
FastAPI app.

`stateless_http=True` + `json_response=True` keep the transport simple:
no event store, no SSE long-poll for now, every request gets a fully-
buffered JSON response.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from market_analyser.annotations.types import (
    SUPPORTED_TIMEFRAMES,
    Annotation,
    AnnotationKind,
)
from market_analyser.api.backfill_response import BackfillOhlcvResponse, GetOhlcvResponse
from market_analyser.api.events import (
    ChartHighlightPayloadV1,
    ChartShowPayloadV1,
    ChartUpdatePayloadV1,
    EventBus,
    GapWindow,
    Marker,
    OverlaySpec,
)
from market_analyser.api.mcp_tools.crypto_fear_greed import register_crypto_fear_greed
from market_analyser.api.mcp_tools.news_for import register_news_for
from market_analyser.api.mcp_tools.run_backtest import register_run_backtest
from market_analyser.api.mcp_tools.screener_query import register_screener_query
from market_analyser.api.mcp_tools.sentiment_for_news import register_sentiment_for_news
from market_analyser.api.mcp_tools.stocktwits_sentiment import register_stocktwits_sentiment
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)


def _require_supported_timeframe(timeframe: str) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"timeframe {timeframe!r} not supported (supported: {sorted(SUPPORTED_TIMEFRAMES)})",
        )


def _require_non_empty_symbol(symbol: str) -> None:
    if not symbol:
        raise ValueError("symbol must be a non-empty string")


def _require_ordered_range(range_start: datetime | None, range_end: datetime | None) -> None:
    if range_start is not None and range_end is not None and range_end < range_start:
        raise ValueError(
            f"range_end {range_end.isoformat()} must be >= range_start {range_start.isoformat()}",
        )


def _parse_overlays(raw: list[dict[str, Any]] | None) -> list[OverlaySpec] | None:
    if raw is None:
        return None
    return [OverlaySpec.model_validate(item) for item in raw]


# The tool docstrings are agent UX (ADR-0015): the agent reads these to decide
# whether get_ohlcv can populate the cache. Plan 0013 fixes the old "from the
# local cache" wording that made the agent treat get_ohlcv as cache-only.
GET_OHLCV_DESCRIPTION = (
    "Read OHLCV bars for one symbol over a [start, end] window. Reads the local "
    "cache and fetches any missing bars from the upstream (Yahoo) on a cache "
    "miss before returning, so this tool populates the cache itself — no separate "
    "step is needed. Returns {bars, partial_reason, message}: partial_reason is "
    "null on full success, or a typed reason (rate_limited | upstream_unavailable "
    "| unknown_symbol) when only some gaps could be filled. Set backfill_async="
    "true to return whatever is already cached immediately and run the fetch in "
    "the background (partial_reason='backfill_async_pending'); progress then "
    "arrives on the event stream as ohlcv.backfilled / ohlcv.backfill_failed. "
    "Live-mode only; supported timeframes: 1d, 1h."
)

BACKFILL_OHLCV_DESCRIPTION = (
    "Pre-warm the local cache for a symbol/timeframe over [start, end] by "
    "fetching any missing bars from the upstream in the background. Returns "
    "immediately with {started, gaps, message}: started=true plus the gap "
    "windows when a background fetch was scheduled, or started=false and an "
    "empty gaps list when the cache already covers the window. Watch the event "
    "stream — ohlcv.backfill_started fires first, then ohlcv.backfilled on "
    "success or ohlcv.backfill_failed (reason: rate_limited | upstream_unavailable "
    "| unknown_symbol) on failure."
)


async def _get_ohlcv_response(
    *,
    provider: MarketDataProvider,
    coordinator: BackfillCoordinator | None,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    backfill_async: bool,
) -> GetOhlcvResponse:
    """Body of the `get_ohlcv` tool, factored out so the backfill paths are unit-
    testable on a single event loop (no live MCP server needed for the event
    assertions). Sync mode preserves today's fetch-on-miss behaviour."""
    # Validate at the MCP boundary like backfill_ohlcv does — bad input must
    # raise here, not slip into the async path where it would publish a
    # `started` event and then die without a `failed` (leaving the spinner stuck).
    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(start, end)
    if backfill_async:
        if coordinator is None:
            raise ValueError("backfill_async=true requires a cache-coverage-capable provider")
        cov = coordinator.coverage(symbol, timeframe, start, end)
        if not cov.gaps:
            # Cache already complete — return it, schedule nothing, publish nothing.
            return GetOhlcvResponse(bars=list(cov.cached), partial_reason=None, message=None)
        coordinator.schedule(symbol, timeframe, start, end)
        return GetOhlcvResponse(
            bars=list(cov.cached),
            partial_reason="backfill_async_pending",
            message=(
                "returned cached bars; a background backfill was scheduled — watch "
                "ohlcv.backfilled / ohlcv.backfill_failed on the event stream"
            ),
        )
    # Sync mode (default): fetch-on-miss, offloaded so it never blocks the loop.
    # With a coverage-capable provider, surface partial failures (some gaps
    # fetched, some failed) instead of failing loud; else fall back to the plain
    # fetch (legacy / coverage-less stub providers).
    if coordinator is not None:
        result = await asyncio.to_thread(
            coordinator.get_ohlcv_with_status, symbol, timeframe, start, end
        )
        return GetOhlcvResponse(
            bars=list(result.bars),
            partial_reason=result.partial_reason,
            message=result.message,
        )
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end)
    return GetOhlcvResponse(bars=list(bars), partial_reason=None, message=None)


async def _backfill_ohlcv_response(
    *,
    coordinator: BackfillCoordinator | None,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> BackfillOhlcvResponse:
    """Body of the `backfill_ohlcv` tool, factored out for single-loop unit tests.
    Validates input at the boundary, then schedules a background fetch only when
    the cache actually has gaps."""
    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(start, end)
    if coordinator is None:
        raise ValueError("backfill_ohlcv requires a cache-coverage-capable provider")
    cov = coordinator.coverage(symbol, timeframe, start, end)
    if not cov.gaps:
        return BackfillOhlcvResponse(
            started=False,
            gaps=[],
            message="cache already covers the requested window; nothing to fetch",
        )
    coordinator.schedule(symbol, timeframe, start, end)
    return BackfillOhlcvResponse(
        started=True,
        gaps=[GapWindow(start=gap_start, end=gap_end) for gap_start, gap_end in cov.gaps],
        message=(
            "backfill scheduled in the background — watch ohlcv.backfilled / "
            "ohlcv.backfill_failed on the event stream"
        ),
    )


def create_mcp_components(
    *,
    provider: MarketDataProvider,
    annotations_repository: AnnotationsRepository,
    event_bus: EventBus,
    backfill_coordinator: BackfillCoordinator | None = None,
    backtest_runs_repository: BacktestRunsRepository | None = None,
    runs_dir: Path | None = None,
) -> tuple[StreamableHTTPSessionManager, StreamableHTTPASGIApp]:
    """Build the FastMCP server and return its session manager + ASGI handler.

    Returns `(session_manager, asgi_app)`. The caller must:
    - run `session_manager.run()` as part of the FastAPI lifespan (without this,
      the first request raises "Task group is not initialized"); and
    - mount `asgi_app` at `/mcp` as a single ASGI route (not a Mount, to avoid
      the trailing-slash redirect).

    The `run_backtest` tool (Plan 0008 phase 4) is registered when both
    `backtest_runs_repository` and `runs_dir` are supplied. Either alone is
    insufficient — the tool needs both the SQLite index and the disk root.
    Legacy callers that omit them keep the pre-Plan-0008 toolset; nothing
    silently degrades.
    """
    server = FastMCP(
        name="market-analyser",
        stateless_http=True,
        json_response=True,
    )

    # `backfill_coordinator` is constructed by create_app (bound to
    # app.state.backfill_coordinator) when the provider is coverage-capable;
    # None for coverage-less stub providers, in which case the backfill paths
    # refuse with a clear error and the sync get_ohlcv falls back to the plain
    # fetch.

    @server.tool(description=GET_OHLCV_DESCRIPTION)
    async def get_ohlcv(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        backfill_async: bool = False,
    ) -> GetOhlcvResponse:
        return await _get_ohlcv_response(
            provider=provider,
            coordinator=backfill_coordinator,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            backfill_async=backfill_async,
        )

    @server.tool(description=BACKFILL_OHLCV_DESCRIPTION)
    async def backfill_ohlcv(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BackfillOhlcvResponse:
        return await _backfill_ohlcv_response(
            coordinator=backfill_coordinator,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )

    @server.tool(
        description=(
            "Write a chart annotation (bullish/bearish marker on a single "
            "candle). Returns the persisted record with its id and created_at. "
            "`label` is the hover text; `agent_id` is your opaque identifier "
            "(defaults to 'unknown')."
        ),
    )
    def write_annotation(
        symbol: str,
        timeframe: str,
        event_ts: datetime,
        kind: AnnotationKind,
        label: str | None = None,
        agent_id: str = "unknown",
    ) -> Annotation:
        annotation = Annotation(
            symbol=symbol,
            timeframe=timeframe,
            event_ts=event_ts,
            kind=kind,
            label=label,
            agent_id=agent_id,
        )
        annotations_repository.insert(annotation)
        return annotation

    @server.tool(
        description=(
            "List annotations for a symbol/timeframe over a [start, end] window. "
            "Boundary-inclusive on both ends. Returns annotations from all agents "
            "(no per-agent_id filter)."
        ),
    )
    def list_annotations(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Annotation]:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"timeframe {timeframe!r} not supported "
                f"(supported: {sorted(SUPPORTED_TIMEFRAMES)})",
            )
        return annotations_repository.list_for(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )

    @server.tool(
        description=(
            "Render a chart in the Electron viewer. Publishes a `chart.show v1` "
            "event to the SSE stream. The renderer mounts/switches to the "
            "requested symbol+timeframe and renders the requested window with "
            "the supplied overlays. Returns immediately whether or not a viewer "
            "is connected — events are ephemeral; reopening Electron after a "
            "call to this tool will not replay it."
        ),
    )
    def show_chart(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        overlays: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        _require_supported_timeframe(timeframe)
        _require_ordered_range(range_start, range_end)
        payload = ChartShowPayloadV1(
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            overlays=_parse_overlays(overlays),
        )
        event_bus.publish("chart.show", payload)
        return {
            "event_published": True,
            "type": "chart.show",
            "version": ChartShowPayloadV1.VERSION,
        }

    @server.tool(
        description=(
            "Apply a delta to the currently-rendered chart. Publishes a "
            "`chart.update v1` event. Any subset of {overlays, range_start, "
            "range_end, focus_bar} may be supplied; unset fields are not "
            "carried on the wire (the renderer merges the delta into its "
            "current state). If no chart for `symbol`+`timeframe` is currently "
            "open in the viewer, the renderer treats this as a `chart.show`."
        ),
    )
    def update_chart(
        symbol: str,
        timeframe: str,
        overlays: list[dict[str, Any]] | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        focus_bar: datetime | None = None,
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        _require_supported_timeframe(timeframe)
        _require_ordered_range(range_start, range_end)
        payload = ChartUpdatePayloadV1(
            symbol=symbol,
            timeframe=timeframe,
            overlays=_parse_overlays(overlays),
            range_start=range_start,
            range_end=range_end,
            focus_bar=focus_bar,
        )
        event_bus.publish("chart.update", payload)
        return {
            "event_published": True,
            "type": "chart.update",
            "version": ChartUpdatePayloadV1.VERSION,
        }

    @server.tool(
        description=(
            "Highlight a pattern on a chart. Publishes a `chart.highlight v1` "
            "event AND persists each marker as an annotation row (so the "
            "highlight survives a viewer reload). Use this for patterns you "
            "detected NOW; use `write_annotation` for the lower-level "
            "persist-only primitive."
        ),
    )
    def highlight_pattern(
        symbol: str,
        timeframe: str,
        event_ts: datetime,
        kind: AnnotationKind,
        label: str | None = None,
        agent_id: str = "unknown",
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        _require_supported_timeframe(timeframe)
        # `AnnotationKind` is a `StrEnum`; its value is one of the literal
        # strings `Marker.kind` accepts. Pydantic accepts the enum at runtime,
        # but mypy can't widen the StrEnum to the Literal — coerce to plain
        # str so the type-checker sees the narrowed value.
        marker = Marker(event_ts=event_ts, kind=str(kind), label=label)  # type: ignore[arg-type]
        payload = ChartHighlightPayloadV1(
            symbol=symbol,
            timeframe=timeframe,
            markers=[marker],
        )
        # Persist first (so re-opening Electron sees the marker via the
        # annotations table), then publish (so the live viewer renders it
        # immediately). Failure to persist surfaces as an MCP error before
        # the live event lands.
        annotation = Annotation(
            symbol=symbol,
            timeframe=timeframe,
            event_ts=event_ts,
            kind=kind,
            label=label,
            agent_id=agent_id,
        )
        annotations_repository.insert(annotation)
        event_bus.publish("chart.highlight", payload)
        return {
            "event_published": True,
            "type": "chart.highlight",
            "version": ChartHighlightPayloadV1.VERSION,
        }

    # Always registered (no extra deps) — these dispatch through the provider
    # Protocol; the adapters stay package-internal (ADR-0007).
    register_screener_query(server, provider=provider)
    register_news_for(server, provider=provider)
    register_sentiment_for_news(server, provider=provider)
    register_crypto_fear_greed(server, provider=provider)
    register_stocktwits_sentiment(server, provider=provider)

    if backtest_runs_repository is not None and runs_dir is not None:
        register_run_backtest(
            server,
            provider=provider,
            repository=backtest_runs_repository,
            event_bus=event_bus,
            runs_dir=runs_dir,
        )

    # streamable_http_app() also lazily constructs the session manager; we call
    # it for that side effect even though we discard the returned Starlette app.
    server.streamable_http_app()
    session_manager = server.session_manager
    asgi_app = StreamableHTTPASGIApp(session_manager)
    return session_manager, asgi_app
