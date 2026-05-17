"""FastAPI application factory and bearer-auth middleware.

Auth contract (per ADR-0002 and Plan 0001 slice 1): every request must carry
`Authorization: Bearer <secret>` except `GET /healthz`, which is auth-exempt
so the Electron supervisor can probe liveness without holding the secret.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from market_analyser import __version__

AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/healthz"})


def create_app(*, secret: str) -> FastAPI:
    """Build the FastAPI app with the bearer-auth middleware bound to `secret`."""
    if not secret:
        raise ValueError("create_app requires a non-empty bearer secret")

    app = FastAPI(title="market-analyser", version=__version__)

    @app.middleware("http")
    async def bearer_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or token != secret:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "version": __version__}

    return app
