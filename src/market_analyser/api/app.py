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
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from starlette.routing import Route

from market_analyser import __version__
from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.routes.annotations import router as annotations_router
from market_analyser.api.routes.ohlcv import router as ohlcv_router
from market_analyser.api.routes.settings import router as settings_router
from market_analyser.api.routes.settings_stop import router as settings_stop_router
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
    mcp_secret_path: Path | None = None,
    provider: MarketDataProvider | None = None,
    annotations_repository: AnnotationsRepository | None = None,
    engine: Engine | None = None,
    dev_origin: str | None = None,
) -> FastAPI:
    """Build the FastAPI app with the bearer-auth middleware bound to `secret`.

    When `mcp_secret` is provided, the MCP Streamable HTTP sub-app is mounted at
    `/mcp` and gated by that secret. The session manager's `run()` context is
    composed into the FastAPI lifespan; without this the first MCP request
    raises "Task group is not initialized".

    The MCP bearer is stored on `app.state.mcp_secret` (mutable) rather than
    closure-captured by the middleware. Plan 0006 phase 5 needs rotation to
    invalidate active sessions on the next request: the `POST /settings/mcp-
    secret/rotate` route rewrites the file *and* mutates `app.state.mcp_secret`
    in the same handler, so the middleware's next read picks up the new value.

    When `mcp_secret_path` is also provided, the settings routes (GET + POST
    /settings/mcp-secret/...) are registered. Without it, rotation is
    unavailable (no place to write to) and the routes do not exist.

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

    if mcp_secret is not None and annotations_repository is None:
        raise ValueError(
            "create_app requires annotations_repository when mcp_secret is set "
            "(MCP tools read/write annotations)",
        )
    effective_provider = provider if provider is not None else DefaultMarketDataProvider()
    mcp_components = (
        create_mcp_components(
            provider=effective_provider,
            annotations_repository=annotations_repository,
        )
        if mcp_secret is not None and annotations_repository is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if mcp_components is None:
            yield
            return
        session_manager, _asgi_app = mcp_components
        async with session_manager.run():
            yield

    app = FastAPI(title="market-analyser", version=__version__, lifespan=lifespan)
    app.state.provider = effective_provider
    app.state.annotations_repository = annotations_repository
    app.state.mcp_secret = mcp_secret
    app.state.mcp_secret_path = mcp_secret_path

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
            # Read fresh from app.state every request — rotation mutates this in
            # place and the next request must see the new secret. Closure-
            # capturing `mcp_secret` here would break the rotate-invalidates
            # contract from phase 5's done-when.
            current_mcp_secret = request.app.state.mcp_secret
            if current_mcp_secret is None or not secrets.compare_digest(token, current_mcp_secret):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        else:
            if not secrets.compare_digest(token, secret):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    # CORS for Electron's Vite dev origin (e.g. http://localhost:5173). Added
    # AFTER the bearer middleware so Starlette wraps it OUTERMOST and the
    # browser's OPTIONS preflight is short-circuited before bearer 401s it.
    # Loopback-only by the `__main__` validator; unset in packaged builds so
    # this branch never installs in production.
    if dev_origin is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[dev_origin],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            max_age=600,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "version": __version__}

    app.include_router(ohlcv_router)

    if annotations_repository is not None:
        app.include_router(annotations_router)

    if mcp_secret is not None and mcp_secret_path is not None:
        app.include_router(settings_router)

    # `POST /settings/stop` is always registered (no MCP-secret dependency).
    # Renderer-bearer-gated by the central middleware; an agent on `/mcp`
    # cannot stop the sidecar through this route.
    app.include_router(settings_stop_router)

    if mcp_components is not None:
        _, asgi_app = mcp_components
        # Use Route, not Mount: Mount("/mcp", sub_app) issues a 307 redirect
        # from /mcp → /mcp/ which trips simple MCP clients on POST. Route
        # binds exactly to `/mcp` with no path-suffix semantics.
        app.routes.append(
            Route(MCP_PREFIX, endpoint=asgi_app, methods=["GET", "POST", "DELETE"]),
        )

    return app
