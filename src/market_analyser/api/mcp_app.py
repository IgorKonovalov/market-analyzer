"""FastMCP server definition + tool registration (ADR-0014, Plan 0006 phase 1).

This is the walking-skeleton tool surface: a single `ping(message) -> str` tool
that echoes its input. Phase 4 of Plan 0006 replaces it with `get_ohlcv`,
`write_annotation`, and `list_annotations`.

The MCP transport is Streamable HTTP at exactly `/mcp` (no trailing-slash
redirect). We deliberately bypass `FastMCP.streamable_http_app()`'s outer
Starlette wrapper because mounting that on FastAPI under `/mcp` issues a 307
redirect from `/mcp` → `/mcp/` (Starlette's Mount semantics for empty-suffix
paths), which trips simple MCP clients that don't follow redirects for POST.
Instead we surface the inner `StreamableHTTPASGIApp` and register it as a
single ASGI route on the FastAPI app.

`stateless_http=True` + `json_response=True` keep the transport simple: no
event store, no SSE long-poll for now, every request gets a fully-buffered
JSON response.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager


def create_mcp_components() -> tuple[StreamableHTTPSessionManager, StreamableHTTPASGIApp]:
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

    @server.tool(description="Walking-skeleton echo tool (Plan 0006 phase 1; removed in phase 4).")
    def ping(message: str) -> str:
        return message

    # streamable_http_app() also lazily constructs the session manager; we call
    # it for that side effect even though we discard the returned Starlette app.
    server.streamable_http_app()
    session_manager = server.session_manager
    asgi_app = StreamableHTTPASGIApp(session_manager)
    return session_manager, asgi_app
