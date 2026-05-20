"""FastAPI application factory and bearer-auth middleware.

Two co-tenants share this app and this port:

- The renderer routes (`/healthz`, `/ohlcv`, …) are gated by the per-launch
  `MARKET_ANALYSER_SECRET` env-var bearer (ADR-0011).
- The MCP route prefix (`/mcp`, `/mcp/...`) is gated by a long-lived, on-disk
  bearer loaded from `mcp-secret.json` in the user data directory (ADR-0014).

A single middleware dispatches by route prefix and uses `secrets.compare_digest`
for each comparison. A renderer bearer must never authenticate to `/mcp` and
vice versa — Plan 0006 phase 1's done-when asserts this explicitly.

`GET /healthz` remains auth-exempt so the Electron supervisor can probe
liveness without holding either secret.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from starlette.routing import Route

from market_analyser import __version__
from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.routes.annotations import router as annotations_router
from market_analyser.api.routes.ohlcv import router as ohlcv_router
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.provider import MarketDataProvider
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import apply_migrations, make_session_factory
from market_analyser.persistence.repository import BarRepository

AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/healthz"})
MCP_PREFIX = "/mcp"


def create_app(
    *,
    secret: str,
    mcp_secret: str | None = None,
    provider: MarketDataProvider | None = None,
    annotations_repository: AnnotationsRepository | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    """Build the FastAPI app with the bearer-auth middleware bound to `secret`.

    When `mcp_secret` is provided, the MCP Streamable HTTP sub-app is mounted at
    `/mcp` and gated by that secret. The session manager's `run()` context is
    composed into the FastAPI lifespan; without this the first MCP request
    raises "Task group is not initialized".

    When `mcp_secret` is `None` (e.g. legacy tests), `/mcp` is unmounted and any
    request to a `/mcp*` path returns 401 (no MCP secret configured), keeping
    the cross-tenant guarantee even when MCP is disabled.

    If `engine` is supplied, Alembic migrations are applied immediately (so a
    broken migration aborts startup, not first-request) and — unless `provider`
    is also supplied — a cache-aware `DefaultMarketDataProvider` is built on top.
    """
    if not secret:
        raise ValueError("create_app requires a non-empty bearer secret")

    if engine is not None:
        apply_migrations(engine)
        session_factory = make_session_factory(engine)
        if provider is None:
            provider = DefaultMarketDataProvider(bar_repository=BarRepository(session_factory))
        if annotations_repository is None:
            annotations_repository = AnnotationsRepository(session_factory)

    mcp_components = create_mcp_components() if mcp_secret is not None else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if mcp_components is None:
            yield
            return
        session_manager, _asgi_app = mcp_components
        async with session_manager.run():
            yield

    app = FastAPI(title="market-analyser", version=__version__, lifespan=lifespan)
    app.state.provider = provider if provider is not None else DefaultMarketDataProvider()
    app.state.annotations_repository = annotations_repository

    @app.middleware("http")
    async def bearer_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        is_mcp = path == MCP_PREFIX or path.startswith(MCP_PREFIX + "/")
        if is_mcp:
            if mcp_secret is None or not secrets.compare_digest(token, mcp_secret):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        else:
            if not secrets.compare_digest(token, secret):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "version": __version__}

    app.include_router(ohlcv_router)

    if annotations_repository is not None:
        app.include_router(annotations_router)

    if mcp_components is not None:
        _, asgi_app = mcp_components
        # Use Route, not Mount: Mount("/mcp", sub_app) issues a 307 redirect
        # from /mcp → /mcp/ which trips simple MCP clients on POST. Route
        # binds exactly to `/mcp` with no path-suffix semantics.
        app.routes.append(
            Route(MCP_PREFIX, endpoint=asgi_app, methods=["GET", "POST", "DELETE"]),
        )

    return app
