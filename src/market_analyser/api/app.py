"""FastAPI application factory and bearer-auth middleware.

Auth contract (per ADR-0002 and Plan 0001 phase 1): every request must carry
`Authorization: Bearer <secret>` except `GET /healthz`, which is auth-exempt
so the Electron supervisor can probe liveness without holding the secret.

Phase 2 adds the `MarketDataProvider` on `app.state.provider`. Phase 3 wires
Alembic migrations on startup and builds a cache-aware provider when an
`engine` is supplied.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from market_analyser import __version__
from market_analyser.api.routes.ohlcv import router as ohlcv_router
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.provider import MarketDataProvider
from market_analyser.persistence.engine import apply_migrations, make_session_factory
from market_analyser.persistence.repository import BarRepository

AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/healthz"})


def create_app(
    *,
    secret: str,
    provider: MarketDataProvider | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    """Build the FastAPI app with the bearer-auth middleware bound to `secret`.

    If `engine` is supplied, Alembic migrations are applied immediately (so a
    broken migration aborts startup, not first-request) and — unless `provider`
    is also supplied — a cache-aware `DefaultMarketDataProvider` is built on top.

    Tests typically pass `provider=FakeMarketDataProvider(...)` and omit `engine`.
    """
    if not secret:
        raise ValueError("create_app requires a non-empty bearer secret")

    if engine is not None:
        apply_migrations(engine)
        if provider is None:
            repo = BarRepository(make_session_factory(engine))
            provider = DefaultMarketDataProvider(bar_repository=repo)

    app = FastAPI(title="market-analyser", version=__version__)
    app.state.provider = provider if provider is not None else DefaultMarketDataProvider()

    @app.middleware("http")
    async def bearer_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, secret):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "version": __version__}

    app.include_router(ohlcv_router)

    return app
