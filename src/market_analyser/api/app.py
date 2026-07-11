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

import asyncio
import contextlib
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
from market_analyser.alerts.scheduler import WatchScheduler
from market_analyser.api.advice_backfill import backfill_advice_ledger
from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.routes.agent_mode import router as agent_mode_router
from market_analyser.api.routes.annotations import router as annotations_router
from market_analyser.api.routes.backtests import router as backtests_router
from market_analyser.api.routes.defi import router as defi_router
from market_analyser.api.routes.events import router as events_router
from market_analyser.api.routes.news import router as news_router
from market_analyser.api.routes.ohlcv import router as ohlcv_router
from market_analyser.api.routes.quote import router as quote_router
from market_analyser.api.routes.scan_chart_patterns import router as scan_chart_patterns_router
from market_analyser.api.routes.scan_patterns import router as scan_patterns_router
from market_analyser.api.routes.search import router as search_router
from market_analyser.api.routes.settings import router as settings_router
from market_analyser.api.routes.settings_stop import router as settings_stop_router
from market_analyser.api.routes.track_record import router as track_record_router
from market_analyser.api.routes.ui_events import router as ui_events_router
from market_analyser.api.routes.watches import router as watches_router
from market_analyser.api.sse_ticket import SseTicketStore
from market_analyser.api.ui_events.agent_mode import AGENT_MODE_FILENAME, AgentModeStore
from market_analyser.attribution.scoring_job import (
    DEFAULT_INTERVAL_SECONDS as SCORING_DEFAULT_INTERVAL_SECONDS,
)
from market_analyser.attribution.scoring_job import RecommendationScoringJob
from market_analyser.config import default_app_data_dir
from market_analyser.data.adapters.binance_account import BinanceAccountAdapter
from market_analyser.data.adapters.binance_derivatives import BinanceDerivativesAdapter
from market_analyser.data.adapters.binance_klines import BinanceKlinesAdapter
from market_analyser.data.adapters.coinbase import CoinbaseAdapter
from market_analyser.data.adapters.coingecko import CoinGeckoAdapter
from market_analyser.data.adapters.coinmetrics import CoinMetricsCommunityAdapter
from market_analyser.data.adapters.crypto_fear_greed import CryptoFearGreedAdapter
from market_analyser.data.adapters.defillama import DefiLlamaAdapter
from market_analyser.data.adapters.lp_detail import RpcLpDetailAdapter
from market_analyser.data.adapters.onchain_pools import OnchainPoolPriceAdapter
from market_analyser.data.adapters.zerion import ZerionAdapter
from market_analyser.data.adapters.zerion_tx import ZerionTxAdapter
from market_analyser.data.backfill import BackfillCoordinator, SupportsBackfill
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.metric_accrual import (
    DEFAULT_INTERVAL_SECONDS,
    MetricAccrualJob,
    MetricAccrualSources,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import (
    AccountHoldingsSource,
    HistoricalPriceSource,
    LpPositionDetailSource,
    PoolPriceSource,
    TxHistorySource,
    WalletPositionsSource,
)
from market_analyser.events import EventBus
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerRepository
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.engine import apply_migrations, make_session_factory
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)
from market_analyser.persistence.repository import BarRepository
from market_analyser.persistence.secrets import SecretsStore
from market_analyser.ui_events.buffer import UIEventBuffer

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
    pool_price_sources: Mapping[str, PoolPriceSource] | None = None,
    tx_history_sources: Mapping[str, TxHistorySource] | None = None,
    historical_price_source: HistoricalPriceSource | None = None,
    account_holdings_sources: Mapping[str, AccountHoldingsSource] | None = None,
    manual_positions_path: Path | None = None,
    provider: MarketDataProvider | None = None,
    annotations_repository: AnnotationsRepository | None = None,
    backtest_runs_repository: BacktestRunsRepository | None = None,
    runs_dir: Path | None = None,
    engine: Engine | None = None,
    dev_origin: str | None = None,
    event_bus: EventBus | None = None,
    sse_ticket_store: SseTicketStore | None = None,
    agent_mode_path: Path | None = None,
    metric_accrual_enabled: bool = False,
    metric_accrual_interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    metric_accrual_sources: MetricAccrualSources | None = None,
    recommendation_scoring_enabled: bool = False,
    recommendation_scoring_interval_seconds: int = SCORING_DEFAULT_INTERVAL_SECONDS,
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

    metric_points_repository: MetricPointsRepository | None = None
    watches_repository: WatchesRepository | None = None
    alerts_repository: AlertsRepository | None = None
    defi_tx_repository: DefiTxRepository | None = None
    advice_ledger_repository: AdviceLedgerRepository | None = None
    if engine is not None:
        apply_migrations(engine)
        session_factory = make_session_factory(engine)
        # The metric store (Plan 0055, ADR-0051) backs the historized series:
        # the F&G / dominance adapters write-through into it (composition-root
        # wiring per ADR-0031), and the metric-series MCP tools read from it.
        metric_points_repository = MetricPointsRepository(session_factory)
        # The alerting stores (Plan 0060, ADR-0055): watch definitions the
        # lifespan scheduler ticks + the append-only fire history. Built
        # whenever persistence exists — the watch MCP toolset and the
        # scheduler both key off these.
        watches_repository = WatchesRepository(session_factory)
        alerts_repository = AlertsRepository(session_factory)
        # The P&L caches (Plan 0035, ADR-0036): the immutable decoded-tx store
        # behind the gap-fetch ingestion, and the first-write-wins price
        # snapshots that make a replay revision-proof. The DefiLlama adapter is
        # keyless and network-free to construct; only a fetch touches the wire.
        defi_tx_repository = DefiTxRepository(session_factory)
        if historical_price_source is None:
            historical_price_source = DefiLlamaAdapter(
                snapshot_store=PriceSnapshotRepository(session_factory),
            )
        if provider is None:
            provider = DefaultMarketDataProvider(
                bar_repository=BarRepository(session_factory),
                crypto_fng=CryptoFearGreedAdapter(metric_store=metric_points_repository),
                coingecko=CoinGeckoAdapter(metric_store=metric_points_repository),
                # Binance klines (Plan 0058 / ADR-0052): wired only here, in the
                # composition root (ADR-0031) — the membership check may lazily
                # fetch exchangeInfo, so an unwired provider (tests) never
                # reaches the network. The symbol-set cache persists alongside
                # the other app data.
                binance=BinanceKlinesAdapter(
                    symbol_cache_path=default_app_data_dir() / "binance_exchange_info.json",
                ),
                # Coinbase (Plan 0081 / ADR-0076): the USD-native crypto source,
                # third in the Binance → Coinbase → Yahoo membership routing.
                # Wired only here — like Binance, the membership check may lazily
                # fetch the product set, so an unwired provider (tests) never
                # reaches the network. Its product-set cache persists alongside
                # the other app data.
                coinbase=CoinbaseAdapter(
                    symbol_cache_path=default_app_data_dir() / "coinbase_products.json",
                ),
            )
        if annotations_repository is None:
            annotations_repository = AnnotationsRepository(session_factory)
        if backtest_runs_repository is None:
            backtest_runs_repository = BacktestRunsRepository(session_factory)
        # The advisor track-record ledger (Plan 0080, ADR-0075): the append-only
        # index the `recommend` tool writes a row into per call, and the phase-3
        # scorer later fills with outcomes. Built whenever persistence exists.
        advice_ledger_repository = AdviceLedgerRepository(session_factory)
        # One-shot back-fill of the pre-existing runs/advice artifacts (ADR-0058)
        # into the ledger, so the record starts with history rather than empty.
        # Idempotent (first-write-wins), so it runs safely on every boot.
        if runs_dir is not None:
            backfill_advice_ledger(advice_ledger_repository, runs_dir)

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
    elif secrets_store is not None:
        effective_lp_detail_sources = {"rpc": RpcLpDetailAdapter(secrets_store=secrets_store)}
    else:
        effective_lp_detail_sources = {}
    # DeFi pool-price sources (Plan 0079, ADR-0031/0072): the read-only selector
    # registry behind the cross-pool discrepancy scanner (the arb-viability
    # evidence layer). An explicit map wins (tests inject a fake); otherwise the
    # on-chain adapter is built from the secrets store (lazy RPC-URL read, so it
    # constructs before a URL is set — a scan without one fails typed at call
    # time). Its default pool set is empty until a live evidence run (phase 4)
    # supplies verified pool addresses, so an out-of-the-box scan reports zero
    # configured pools rather than pricing fabricated ones. Empty when no store is
    # wired.
    if pool_price_sources is not None:
        effective_pool_price_sources: dict[str, PoolPriceSource] = dict(pool_price_sources)
    elif secrets_store is not None:
        effective_pool_price_sources = {
            "onchain": OnchainPoolPriceAdapter(secrets_store=secrets_store),
        }
    else:
        effective_pool_price_sources = {}
    # DeFi tx-history sources (Plan 0035, ADR-0031/0035/0036): the selector
    # registry behind the P&L ingestion path. An explicit map wins (tests inject
    # a fake); otherwise the Zerion tx adapter is built from the secrets store
    # (lazy key read, so it constructs before a key is set — a keyless pull
    # fails typed at call time). Empty when no store is wired — the P&L surface
    # (phase 7) treats an absent source as "P&L unavailable", not a crash.
    if tx_history_sources is not None:
        effective_tx_history_sources: dict[str, TxHistorySource] = dict(tx_history_sources)
    elif secrets_store is not None:
        effective_tx_history_sources = {"zerion": ZerionTxAdapter(secrets_store=secrets_store)}
    else:
        effective_tx_history_sources = {}
    # Venue account-holdings sources (Plan 0041, ADR-0042): the cross-venue
    # portfolio's CEX leg. An explicit map wins (tests inject a fake); otherwise
    # the read-only Binance account adapter is built from the secrets store
    # (lazy key read — it constructs before a key is set; a keyless read fails
    # typed at call time). Empty when no store is wired — `portfolio_summary`
    # is then simply not registered, nothing silently degrades.
    if account_holdings_sources is not None:
        effective_account_sources: dict[str, AccountHoldingsSource] = dict(account_holdings_sources)
    elif secrets_store is not None:
        effective_account_sources = {"binance": BinanceAccountAdapter(secrets_store=secrets_store)}
    else:
        effective_account_sources = {}
    mcp_components = (
        create_mcp_components(
            provider=effective_provider,
            annotations_repository=annotations_repository,
            event_bus=effective_event_bus,
            ui_event_buffer=ui_event_buffer,
            backfill_coordinator=backfill_coordinator,
            backtest_runs_repository=backtest_runs_repository,
            advice_ledger_repository=advice_ledger_repository,
            runs_dir=runs_dir,
            wallet_positions_sources=effective_wallet_sources,
            lp_detail_sources=effective_lp_detail_sources,
            pool_price_sources=effective_pool_price_sources,
            tx_history_sources=effective_tx_history_sources,
            defi_tx_repository=defi_tx_repository,
            historical_price_source=historical_price_source,
            metric_points_repository=metric_points_repository,
            watches_repository=watches_repository,
            alerts_repository=alerts_repository,
            account_holdings_sources=effective_account_sources,
            manual_positions_path=manual_positions_path,
        )
        if mcp_secret is not None and annotations_repository is not None
        else None
    )

    # The watch scheduler (Plan 0060, ADR-0055): the alerting clock, started
    # and stopped with the app lifespan below. Constructed only when the
    # alerting repositories exist (i.e. persistence is wired) — a repo-less
    # test app has no watches to tick and no scheduler either.
    watch_scheduler = (
        WatchScheduler(
            watches_repository=watches_repository,
            alerts_repository=alerts_repository,
            provider=effective_provider,
            event_bus=effective_event_bus,
            ui_event_buffer=ui_event_buffer,
            backfill_coordinator=backfill_coordinator,
        )
        if watches_repository is not None and alerts_repository is not None
        else None
    )

    # The metric-accrual job (Plan 0061, ADR-0056): the self-warming clock for
    # the five v2 exogenous series, started and stopped with the app lifespan
    # below. Constructed only when the metric store exists (persistence wired)
    # AND the caller opted in — disabled or persistence-free, no job, and the
    # sources (fakes in tests, real adapters here) are never touched. The
    # product-level on-by-default lives in `AppConfig.metric_accrual_enabled`
    # (__main__ passes it through); this factory's parameter defaults to False
    # because the job ticks immediately at startup — an engine-wired test app
    # that never asked for accrual must never reach the network. All four real
    # adapters construct network-free; only a tick reaches the wire.
    if metric_accrual_enabled and metric_points_repository is not None:
        if metric_accrual_sources is None:
            derivatives_adapter = BinanceDerivativesAdapter(
                metric_store=metric_points_repository,
            )
            metric_accrual_sources = MetricAccrualSources(
                fng=CryptoFearGreedAdapter(metric_store=metric_points_repository),
                macro=CoinGeckoAdapter(metric_store=metric_points_repository),
                funding=derivatives_adapter,
                open_interest=derivatives_adapter,
                mvrv=CoinMetricsCommunityAdapter(metric_store=metric_points_repository),
            )
        metric_accrual_job: MetricAccrualJob | None = MetricAccrualJob(
            metric_store=metric_points_repository,
            sources=metric_accrual_sources,
            interval_seconds=metric_accrual_interval_seconds,
        )
    else:
        metric_accrual_job = None

    # The recommendation scorer (Plan 0080, ADR-0075): the track-record clock,
    # started and stopped with the app lifespan below. Constructed only when the
    # ledger exists (persistence wired) AND the caller opted in — like the accrual
    # job, this factory's parameter defaults to False because the job ticks
    # immediately at startup (tick-first, ADR-0056), and an engine-wired test app
    # that never asked for scoring must never reach the network to fetch bars. The
    # product-level on-by-default lives in `AppConfig.recommendation_scoring_enabled`
    # (__main__ passes it through).
    if recommendation_scoring_enabled and advice_ledger_repository is not None:
        recommendation_scoring_job: RecommendationScoringJob | None = RecommendationScoringJob(
            ledger_repository=advice_ledger_repository,
            provider=effective_provider,
            event_bus=effective_event_bus,
            backfill_coordinator=backfill_coordinator,
            interval_seconds=recommendation_scoring_interval_seconds,
        )
    else:
        recommendation_scoring_job = None

    @asynccontextmanager
    async def _accrual_running() -> AsyncIterator[None]:
        # The metric-accrual job rides the lifespan exactly like the watch
        # scheduler (ADR-0056 via the ADR-0055 pattern): separate duty,
        # separate clock, same start/cancel discipline.
        if metric_accrual_job is None:
            yield
            return
        task = asyncio.create_task(metric_accrual_job.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @asynccontextmanager
    async def _scheduler_running() -> AsyncIterator[None]:
        # The watch scheduler rides the lifespan (ADR-0055): started once the
        # app is up, cancelled — and awaited — on shutdown so its in-flight
        # tick finishes or unwinds before the process exits.
        if watch_scheduler is None:
            yield
            return
        task = asyncio.create_task(watch_scheduler.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @asynccontextmanager
    async def _scoring_running() -> AsyncIterator[None]:
        # The recommendation scorer rides the lifespan (ADR-0075 via the ADR-0056
        # pattern): separate duty, separate clock, same start/cancel discipline.
        if recommendation_scoring_job is None:
            yield
            return
        task = asyncio.create_task(recommendation_scoring_job.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # `on_shutdown` callbacks run in a `finally` so process-level cleanup
        # (e.g. lockfile removal from __main__) fires during uvicorn's graceful
        # shutdown — which happens *before* uvicorn re-raises a captured SIGTERM
        # and kills the process. A post-`serve()` `finally` would be unreachable
        # on SIGTERM; this seam is the fix (ADR-0022).
        try:
            if mcp_components is None:
                async with _scheduler_running(), _accrual_running(), _scoring_running():
                    yield
            else:
                session_manager, _asgi_app = mcp_components
                async with (
                    session_manager.run(),
                    _scheduler_running(),
                    _accrual_running(),
                    _scoring_running(),
                ):
                    yield
        finally:
            for shutdown_callback in on_shutdown or ():
                shutdown_callback()

    app = FastAPI(title="market-analyser", version=__version__, lifespan=lifespan)
    app.state.provider = effective_provider
    app.state.annotations_repository = annotations_repository
    app.state.backtest_runs_repository = backtest_runs_repository
    # The advisor track-record ledger (Plan 0080, ADR-0075) — None without an
    # engine. The phase-3 scheduled scorer reads matured rows from it; the
    # `recommend` tool writes rows via create_mcp_components.
    app.state.advice_ledger_repository = advice_ledger_repository
    # The metric-points store (Plan 0055, ADR-0051) — None without an engine;
    # the MCP metric-series tools receive it directly via create_mcp_components.
    app.state.metric_points_repository = metric_points_repository
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
    # The DeFi pool-price registry (Plan 0079) — consumed by the read-only
    # `scan_pool_discrepancies` tool (phase 3) that screens cross-pool spreads
    # net-of-cost as the arb-viability evidence layer.
    app.state.pool_price_sources = effective_pool_price_sources
    # The DeFi tx-history registry (Plan 0035) — consumed by the P&L ingestion
    # path (`POST /defi/pnl` + the `compute_wallet_pnl` tool, phase 7).
    app.state.tx_history_sources = effective_tx_history_sources
    # The P&L pipeline's persistence + pricing halves (Plan 0035): None without
    # an engine — the /defi/pnl route answers 503 rather than crashing.
    app.state.defi_tx_repository = defi_tx_repository
    app.state.historical_price_source = historical_price_source
    # The event bus is the seam between MCP `show_*` tools (phase 3 publishers)
    # and the renderer's `useEventStream` (phase 4 consumer). One per app
    # instance — fresh per test, persistent in production.
    app.state.event_bus = effective_event_bus
    # The SSE ticket store (Plan 0072 phase 4, ADR-0066): mints/consumes the
    # short-lived single-use tickets that authenticate `GET /events` so the
    # durable bearer never rides the stream URL. One per app instance; always
    # present (the middleware and mint route both read it from app.state).
    app.state.sse_ticket_store = (
        sse_ticket_store if sse_ticket_store is not None else SseTicketStore()
    )
    # Plan 0014: the buffer is the renderer→agent seam (POST /ui_events appends;
    # the phase-2 MCP tool/resource read it); the store gates the whole flow.
    app.state.ui_event_buffer = ui_event_buffer
    app.state.agent_mode_store = agent_mode_store
    # The backfill coordinator (Plan 0013) is exposed on app.state so a future
    # phase / route can introspect in-flight backfills; the MCP tools receive it
    # directly via create_mcp_components.
    app.state.backfill_coordinator = backfill_coordinator
    # The watch scheduler (Plan 0060) — None without persistence. /healthz
    # reads its heartbeat; the alerting MCP tools only touch the repositories.
    app.state.watch_scheduler = watch_scheduler
    # The metric-accrual job (Plan 0061, ADR-0056) — None when disabled or
    # persistence-free. /healthz reads its heartbeat; tests drive tick_once.
    app.state.metric_accrual_job = metric_accrual_job
    # The recommendation scorer (Plan 0080, ADR-0075) — None when disabled or
    # persistence-free. /healthz reads its heartbeat; tests drive tick_once.
    app.state.recommendation_scoring_job = recommendation_scoring_job
    # The alerting repositories (Plan 0060 phase 4): consumed by the renderer
    # routes below (watch list, enable/disable, alert history).
    app.state.watches_repository = watches_repository
    app.state.alerts_repository = alerts_repository

    @app.middleware("http")
    async def bearer_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        # The SSE stream authenticates with a short-lived, single-use ticket in
        # the query string (ADR-0066), never the durable bearer: browser
        # `EventSource` can't set headers, and a leaked ticket is worthless in
        # seconds. The ticket is minted by the bearer-gated `POST /events/ticket`
        # and consumed here (single use — one stream per ticket). Every other
        # route, the mint endpoint included, takes the header-bearer path below.
        if path == EVENTS_PATH:
            ticket = request.query_params.get("ticket", "")
            ticket_store: SseTicketStore | None = request.app.state.sse_ticket_store
            if not ticket or ticket_store is None or not ticket_store.consume(ticket):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return await call_next(request)
        # Resolve the bearer token from the Authorization header — the only auth
        # path for every renderer/MCP route now that `/events` uses tickets.
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            token = ""
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
        if watch_scheduler is not None:
            # The alerting heartbeat (Plan 0060, ADR-0055): a wedged scheduler
            # must degrade loudly, and this is the existing health surface.
            # Liveness + error metadata only — no watch definitions, no alert
            # payloads, no secrets (error strings name exception types and
            # watch ids, nothing credential-shaped).
            body["alert_scheduler"] = watch_scheduler.heartbeat().model_dump(mode="json")
        if metric_accrual_job is not None:
            # The self-warming heartbeat (Plan 0061, ADR-0056): per-series
            # freshness must be observable, not discoverable-by-forensics.
            # Liveness + per-series status only — no metric values, no secrets.
            body["metric_accrual"] = metric_accrual_job.heartbeat().model_dump(mode="json")
        if recommendation_scoring_job is not None:
            # The track-record scorer heartbeat (Plan 0080, ADR-0075): a wedged
            # scorer must degrade loudly. Liveness + per-row error metadata only
            # (error strings name exception types and row keys — symbol/timeframe/
            # as-of, nothing credential-shaped); no outcomes, no advice.
            body["recommendation_scoring"] = recommendation_scoring_job.heartbeat().model_dump(
                mode="json"
            )
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
    # `/quote` needs only the provider (always present); renderer-bearer-gated by
    # the central middleware like /ohlcv. The renderer's price header polls it for
    # a live, timeframe-independent current price (Plan 0047).
    app.include_router(quote_router)
    # `POST /scan_patterns` needs the provider + event bus (both always present);
    # renderer-bearer-gated by the central middleware. The "Scan patterns" button
    # posts the visible range; markers arrive via `/events`, mirroring the
    # `scan_patterns` MCP tool through the same pure core (Plan 0049).
    app.include_router(scan_patterns_router)
    # `POST /scan_chart_patterns` (Plan 0064): the trendline sibling of
    # `/scan_patterns` — sweeps classical chart patterns over the visible range
    # and publishes `chart.trendlines` on the bus; the renderer fires it on chart
    # load / range change so the lines track the bars on screen (ADR-0059).
    app.include_router(scan_chart_patterns_router)

    if annotations_repository is not None:
        app.include_router(annotations_router)

    # Backtest routes need both the repository (index) and the runs_dir (disk).
    # Either alone is insufficient; require both before mounting.
    if backtest_runs_repository is not None and runs_dir is not None:
        app.include_router(backtests_router)

    # `GET /track_record` (Plan 0080 phase 5, ADR-0075): the renderer's read
    # surface over the advisor's track record — the REST twin of the
    # `get_track_record` MCP tool. Mounted only when the advice ledger exists
    # (persistence wired), so the route's `app.state.advice_ledger_repository`
    # read is always populated. Renderer-bearer-gated by the central middleware.
    if advice_ledger_repository is not None:
        app.include_router(track_record_router)

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

    # The Alerts surface routes (Plan 0060 phase 4): watch list +
    # enable/disable + alert history. Renderer-bearer-gated by the central
    # middleware; mounted only when the alerting repositories exist.
    if watches_repository is not None and alerts_repository is not None:
        app.include_router(watches_router)

    # `POST /settings/stop` is always registered (no MCP-secret dependency).
    # Renderer-bearer-gated by the central middleware; an agent on `/mcp`
    # cannot stop the sidecar through this route.
    app.include_router(settings_stop_router)

    # `GET /events` SSE stream + `POST /events/ticket` mint. The stream is
    # authenticated by a short-lived single-use ticket in `?ticket=` (ADR-0066),
    # never the durable bearer; the mint endpoint is renderer-bearer-gated by the
    # central middleware, `/events` by the ticket check in that middleware.
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
