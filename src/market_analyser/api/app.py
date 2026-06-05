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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from starlette.routing import Route

from market_analyser import __version__
from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.routes.agent_mode import router as agent_mode_router
from market_analyser.api.routes.annotations import router as annotations_router
from market_analyser.api.routes.backtests import router as backtests_router
from market_analyser.api.routes.defi import router as defi_router
from market_analyser.api.routes.events import router as events_router
from market_analyser.api.routes.news import router as news_router
from market_analyser.api.routes.ohlcv import router as ohlcv_router
from market_analyser.api.routes.search import router as search_router
from market_analyser.api.routes.settings import router as settings_router
from market_analyser.api.routes.settings_stop import router as settings_stop_router
from market_analyser.api.routes.ui_events import router as ui_events_router
from market_analyser.api.ui_events.agent_mode import AGENT_MODE_FILENAME, AgentModeStore
from market_analyser.api.ui_events.buffer import UIEventBuffer
from market_analyser.config import default_app_data_dir
from market_analyser.data.adapters.zerion import ZerionAdapter
from market_analyser.data.backfill import BackfillCoordinator, SupportsBackfill
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import LpPositionDetailSource, WalletPositionsSource
from market_analyser.events import EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import apply_migrations, make_session_factory
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)
from market_analyser.persistence.repository import BarRepository
from market_analyser.persistence.secrets import SecretsStore

AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/healthz"})
MCP_PREFIX = "/mcp"
EVENTS_PATH = "/events"


def create_app(
    *,
    secret: str,
    mcp_secret: str | None = None,
    mcp_secret_path: Path | None = None,
    secrets_store: SecretsStore | None = None,
    wallet_positions_sources: Mapping[str, WalletPositionsSource] | None = None,
    lp_detail_sources: Mapping[str, LpPositionDetailSource] | None = None,
    provider: MarketDataProvider | None = None,
    annotations_repository: AnnotationsRepository | None = None,
    backtest_runs_repository: BacktestRunsRepository | None = None,
    runs_dir: Path | None = None,
    engine: Engine | None = None,
    dev_origin: str | None = None,
    event_bus: EventBus | None = None,
    agent_mode_path: Path | None = None,
    on_shutdown: Sequence[Callable[[], None]] | None = None,
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
        if backtest_runs_repository is None:
            backtest_runs_repository = BacktestRunsRepository(session_factory)

    if mcp_secret is not None and annotations_repository is None:
        raise ValueError(
            "create_app requires annotations_repository when mcp_secret is set "
            "(MCP tools read/write annotations)",
        )
    effective_provider = provider if provider is not None else DefaultMarketDataProvider()
    effective_event_bus = event_bus if event_bus is not None else EventBus()
    # The UI-event buffer + agent-mode store (Plan 0014) back the renderer→agent
    # feedback loop. Always constructed: the `/agent_mode` and `/ui_events`
    # routes are renderer-side and have no MCP-secret dependency. The buffer is
    # in-memory (ephemeral by design, ADR-0021); the store persists the toggle
    # to `<data-dir>/agent_mode.json` — tests pass a tmp path, production wires
    # the canonical data dir from __main__.
    ui_event_buffer = UIEventBuffer()
    agent_mode_store = AgentModeStore(
        agent_mode_path
        if agent_mode_path is not None
        else default_app_data_dir() / AGENT_MODE_FILENAME,
    )
    # The backfill coordinator (Plan 0013) needs the narrow SupportsBackfill
    # capability (get_ohlcv + coverage + get_ohlcv_with_status). The production
    # DefaultMarketDataProvider satisfies it; a coverage-less stub yields None and
    # the MCP backfill paths refuse with a clear error.
    backfill_coordinator = (
        BackfillCoordinator(provider=effective_provider, event_bus=effective_event_bus)
        if isinstance(effective_provider, SupportsBackfill)
        else None
    )
    # DeFi wallet-positions sources (Plan 0032, ADR-0031/0034/0035): the ADR-0031
    # selector registry, keyed by source name. An explicit `wallet_positions_sources`
    # wins (tests inject a fake); otherwise the Zerion adapter is built from the
    # secrets store (it reads its key lazily, so it constructs even before a key is
    # set — a keyless scan fails typed at call time). Empty when no store is wired.
    if wallet_positions_sources is not None:
        effective_wallet_sources: dict[str, WalletPositionsSource] = dict(wallet_positions_sources)
    elif secrets_store is not None:
        effective_wallet_sources = {"zerion": ZerionAdapter(secrets_store=secrets_store)}
    else:
        effective_wallet_sources = {}
    # DeFi LP-detail sources (Plan 0034, ADR-0031/0034): the deep-state selector
    # registry, the depth half of the wallet sources above. An explicit map wins
    # (tests inject a fake); the concrete RPC/Graph deep adapter is built from the
    # secrets store by phase 3. Empty until then — the enrichment step (phase 5)
    # treats an absent source as "discovery-only" rather than failing.
    if lp_detail_sources is not None:
        effective_lp_detail_sources: dict[str, LpPositionDetailSource] = dict(lp_detail_sources)
    else:
        effective_lp_detail_sources = {}
    mcp_components = (
        create_mcp_components(
            provider=effective_provider,
            annotations_repository=annotations_repository,
            event_bus=effective_event_bus,
            ui_event_buffer=ui_event_buffer,
            backfill_coordinator=backfill_coordinator,
            backtest_runs_repository=backtest_runs_repository,
            runs_dir=runs_dir,
            wallet_positions_sources=effective_wallet_sources,
        )
        if mcp_secret is not None and annotations_repository is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # `on_shutdown` callbacks run in a `finally` so process-level cleanup
        # (e.g. lockfile removal from __main__) fires during uvicorn's graceful
        # shutdown — which happens *before* uvicorn re-raises a captured SIGTERM
        # and kills the process. A post-`serve()` `finally` would be unreachable
        # on SIGTERM; this seam is the fix (ADR-0022).
        try:
            if mcp_components is None:
                yield
            else:
                session_manager, _asgi_app = mcp_components
                async with session_manager.run():
                    yield
        finally:
            for shutdown_callback in on_shutdown or ():
                shutdown_callback()

    app = FastAPI(title="market-analyser", version=__version__, lifespan=lifespan)
    app.state.provider = effective_provider
    app.state.annotations_repository = annotations_repository
    app.state.backtest_runs_repository = backtest_runs_repository
    # `runs_dir` is the directory the persist() layer writes artifacts to and
    # the GET /backtests/{run_id} route reads them from. Tests pass a tmp_path;
    # production wires `default_app_data_dir() / "runs"` from __main__.
    app.state.runs_dir = runs_dir
    app.state.mcp_secret = mcp_secret
    app.state.mcp_secret_path = mcp_secret_path
    # The third-party API-key store (Plan 0032, ADR-0038) backs the renderer-only
    # write/status secret endpoints and is read server-side by DeFi adapters.
    # Tests pass a tmp-path store; production wires `<data-dir>/secrets.json`.
    app.state.secrets_store = secrets_store
    # The DeFi wallet-positions registry (built above) is exposed for the
    # `POST /defi/scan` route and the `scan_wallet` tool; the phase-3 scan job
    # consumes the selected source.
    app.state.wallet_positions_sources = effective_wallet_sources
    # The DeFi LP-detail registry (Plan 0034) — consumed by the enrichment step
    # (phase 5) to deepen discovered LP positions with on-chain tick/fee state.
    app.state.lp_detail_sources = effective_lp_detail_sources
    # The event bus is the seam between MCP `show_*` tools (phase 3 publishers)
    # and the renderer's `useEventStream` (phase 4 consumer). One per app
    # instance — fresh per test, persistent in production.
    app.state.event_bus = effective_event_bus
    # Plan 0014: the buffer is the renderer→agent seam (POST /ui_events appends;
    # the phase-2 MCP tool/resource read it); the store gates the whole flow.
    app.state.ui_event_buffer = ui_event_buffer
    app.state.agent_mode_store = agent_mode_store
    # The backfill coordinator (Plan 0013) is exposed on app.state so a future
    # phase / route can introspect in-flight backfills; the MCP tools receive it
    # directly via create_mcp_components.
    app.state.backfill_coordinator = backfill_coordinator

    @app.middleware("http")
    async def bearer_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        # Resolve the bearer token. The Authorization header is the primary
        # path; for `/events` only (ADR-0017), `?token=<bearer>` is also
        # accepted so browser `EventSource` (which can't set custom headers)
        # can subscribe.
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            token = ""
        if not token and path == EVENTS_PATH:
            token = request.query_params.get("token", "")
        if not token:
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
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            max_age=600,
        )

    @app.get("/healthz")
    def healthz(authorization: str | None = Header(default=None)) -> dict[str, object]:
        # Auth-exempt for the unauthenticated liveness probe (spawn-path
        # `waitForHealthz`), but ADR-0020 has the route disclose the resolved
        # `data_dir` to callers who present the renderer bearer so the
        # Electron attach path can confirm sidecar identity. MCP bearer holders
        # are not authorised — `data_dir` is renderer-only.
        body: dict[str, object] = {"ok": True, "version": __version__}
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and token and secrets.compare_digest(token, secret):
                body["data_dir"] = str(default_app_data_dir())
        return body

    app.include_router(ohlcv_router)
    # `/news` needs only the provider (always present, like /ohlcv); renderer-
    # bearer-gated by the central middleware. Unconditional — no repository or
    # runs_dir dependency, unlike /annotations or /backtests (Plan 0023).
    app.include_router(news_router)
    # `/search` needs only the provider (always present); renderer-bearer-gated
    # by the central middleware like /ohlcv (Plan 0024).
    app.include_router(search_router)

    if annotations_repository is not None:
        app.include_router(annotations_router)

    # Backtest routes need both the repository (index) and the runs_dir (disk).
    # Either alone is insufficient; require both before mounting.
    if backtest_runs_repository is not None and runs_dir is not None:
        app.include_router(backtests_router)

    # The settings router carries both the MCP-secret routes (need a secret path)
    # and the third-party API-key routes (need a secrets store). Register it when
    # *either* backing resource is present; each route 503s defensively if its own
    # resource is absent.
    if (mcp_secret is not None and mcp_secret_path is not None) or secrets_store is not None:
        app.include_router(settings_router)

    # `POST /defi/scan` is mounted only when a wallet-positions source is wired
    # (Plan 0032). Renderer-bearer-gated by the central middleware; the agent
    # reaches the same scan job through the `scan_wallet` MCP tool instead.
    if effective_wallet_sources:
        app.include_router(defi_router)

    # `POST /settings/stop` is always registered (no MCP-secret dependency).
    # Renderer-bearer-gated by the central middleware; an agent on `/mcp`
    # cannot stop the sidecar through this route.
    app.include_router(settings_stop_router)

    # `GET /events` SSE stream. Renderer-bearer-gated; query-string ?token=
    # accepted only on this route for EventSource compatibility (ADR-0017).
    app.include_router(events_router)

    # Plan 0014: agent-mode toggle (GET/PUT /agent_mode) + UI-event ingress
    # (POST /ui_events). Renderer-bearer-gated by the central middleware; no
    # MCP-secret dependency, so always registered. The MCP-side read surface
    # lands in phase 2.
    app.include_router(agent_mode_router)
    app.include_router(ui_events_router)

    if mcp_components is not None:
        _, asgi_app = mcp_components
        # Use Route, not Mount: Mount("/mcp", sub_app) issues a 307 redirect
        # from /mcp → /mcp/ which trips simple MCP clients on POST. Route
        # binds exactly to `/mcp` with no path-suffix semantics.
        app.routes.append(
            Route(MCP_PREFIX, endpoint=asgi_app, methods=["GET", "POST", "DELETE"]),
        )

    return app
