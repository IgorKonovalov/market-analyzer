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

from datetime import datetime

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from market_analyser.annotations.types import (
    SUPPORTED_TIMEFRAMES,
    Annotation,
    AnnotationKind,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.persistence.annotations_repository import AnnotationsRepository


def create_mcp_components(
    *,
    provider: MarketDataProvider,
    annotations_repository: AnnotationsRepository,
) -> tuple[StreamableHTTPSessionManager, StreamableHTTPASGIApp]:
    """Build the FastMCP server and return its session manager + ASGI handler.

    Returns `(session_manager, asgi_app)`. The caller must:
    - run `session_manager.run()` as part of the FastAPI lifespan (without this,
      the first request raises "Task group is not initialized"); and
    - mount `asgi_app` at `/mcp` as a single ASGI route (not a Mount, to avoid
      the trailing-slash redirect).
    """
    server = FastMCP(
        name="market-analyser",
        stateless_http=True,
        json_response=True,
    )

    @server.tool(
        description=(
            "Read OHLCV bars from the local cache for a single symbol over a "
            "[start, end] window. Reads are live-mode only (no historical "
            "replay); supported timeframes match the data layer (currently "
            "'1d', '1h')."
        ),
    )
    def get_ohlcv(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        bars = provider.get_ohlcv(symbol=symbol, timeframe=timeframe, start=start, end=end)
        return list(bars)

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

    # streamable_http_app() also lazily constructs the session manager; we call
    # it for that side effect even though we discard the returned Starlette app.
    server.streamable_http_app()
    session_manager = server.session_manager
    asgi_app = StreamableHTTPASGIApp(session_manager)
    return session_manager, asgi_app
