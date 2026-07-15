"""FastMCP server assembly (ADR-0014, Plan 0006 phase 4; consolidated Plan 0017).

`create_mcp_components` is a thin hub: it constructs the `FastMCP` server, calls
one `register_<tool>(server, *, deps)` per tool (each tool lives in its own
`mcp_tools/<tool>.py` module), and wires the Streamable-HTTP transport. It holds
no tool or resource definitions itself — to find a tool, open its module; to add
one, add a module and one `register_*` call here.

Dependencies (provider, repositories, event bus, UI-event buffer) are injected as
keyword arguments and threaded to the relevant `register_*` calls. Tests inject
fakes; production passes the live provider and repos built from the SQLite engine.

The MCP transport is Streamable HTTP at exactly `/mcp` (no trailing-slash
redirect). We deliberately bypass `FastMCP.streamable_http_app()`'s outer
Starlette wrapper because mounting that on FastAPI under `/mcp` issues a 307
redirect from `/mcp` → `/mcp/` (Starlette's Mount semantics for empty-suffix
paths), which trips simple MCP clients that don't follow redirects for POST.
Instead we surface the inner `StreamableHTTPASGIApp` and register it as a single
ASGI route on the FastAPI app.

`stateless_http=True` + `json_response=True` keep the transport simple: no event
store, no SSE long-poll for now, every request gets a fully-buffered JSON
response.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from market_analyser.api.mcp_tools.analyze_symbol import register_analyze_symbol
from market_analyser.api.mcp_tools.annotate_chart import register_annotate_chart
from market_analyser.api.mcp_tools.backfill_ohlcv import register_backfill_ohlcv
from market_analyser.api.mcp_tools.bitcoin_market_pulse import register_bitcoin_market_pulse
from market_analyser.api.mcp_tools.compare_strategies import register_compare_strategies
from market_analyser.api.mcp_tools.compute_wallet_pnl import register_compute_wallet_pnl
from market_analyser.api.mcp_tools.crypto_fear_greed import register_crypto_fear_greed
from market_analyser.api.mcp_tools.cycle_snapshot import register_btc_cycle_snapshot
from market_analyser.api.mcp_tools.derivatives_snapshot import (
    DerivativesSource,
    register_derivatives_snapshot,
)
from market_analyser.api.mcp_tools.detect_chart_patterns import register_detect_chart_patterns
from market_analyser.api.mcp_tools.detect_divergences import register_detect_divergences
from market_analyser.api.mcp_tools.detect_levels import register_detect_levels
from market_analyser.api.mcp_tools.evaluate_signals import register_evaluate_signals
from market_analyser.api.mcp_tools.forecast import register_forecast
from market_analyser.api.mcp_tools.get_backtest import register_get_backtest
from market_analyser.api.mcp_tools.get_chart_drawings import register_get_chart_drawings
from market_analyser.api.mcp_tools.get_ohlcv import register_get_ohlcv
from market_analyser.api.mcp_tools.get_pending_ui_events import register_get_pending_ui_events
from market_analyser.api.mcp_tools.highlight_pattern import register_highlight_pattern
from market_analyser.api.mcp_tools.list_annotations import register_list_annotations
from market_analyser.api.mcp_tools.market_snapshot import register_market_snapshot
from market_analyser.api.mcp_tools.metric_series import register_get_metric_series
from market_analyser.api.mcp_tools.multi_timeframe_analysis import (
    register_multi_timeframe_analysis,
)
from market_analyser.api.mcp_tools.news_for import register_news_for
from market_analyser.api.mcp_tools.pool_discrepancies import register_pool_discrepancies
from market_analyser.api.mcp_tools.portfolio import register_portfolio_summary
from market_analyser.api.mcp_tools.prediction_markets import register_prediction_market_tools
from market_analyser.api.mcp_tools.prediction_screener import register_prediction_screener
from market_analyser.api.mcp_tools.price_structure import register_price_structure
from market_analyser.api.mcp_tools.quote_for import register_quote_for
from market_analyser.api.mcp_tools.recommend import register_recommend
from market_analyser.api.mcp_tools.run_backtest import register_run_backtest
from market_analyser.api.mcp_tools.scan_patterns import register_scan_patterns
from market_analyser.api.mcp_tools.scan_wallet import register_scan_wallet
from market_analyser.api.mcp_tools.scan_watchlist import register_scan_watchlist
from market_analyser.api.mcp_tools.screener_query import register_screener_query
from market_analyser.api.mcp_tools.search_symbols import register_search_symbols
from market_analyser.api.mcp_tools.sentiment import register_sentiment
from market_analyser.api.mcp_tools.show_chart import register_show_chart
from market_analyser.api.mcp_tools.technical_read import register_technical_read
from market_analyser.api.mcp_tools.track_record import register_get_track_record
from market_analyser.api.mcp_tools.update_chart import register_update_chart
from market_analyser.api.mcp_tools.volume_read import register_volume_read
from market_analyser.api.mcp_tools.walk_forward_backtest import register_walk_forward_backtest
from market_analyser.api.mcp_tools.watches import register_watch_tools
from market_analyser.api.mcp_tools.write_annotation import register_write_annotation
from market_analyser.data.adapters.binance_derivatives import BinanceDerivativesAdapter
from market_analyser.data.adapters.coinmetrics import CoinMetricsCommunityAdapter
from market_analyser.data.adapters.polymarket import PolymarketOddsAdapter
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import (
    AccountHoldingsSource,
    ExecutableQuoteSource,
    GaugeResolutionSource,
    HistoricalPriceSource,
    LpPositionDetailSource,
    MetricSeriesSource,
    PredictionMarketSource,
    TxHistorySource,
    UnclaimedRewardsSource,
    WalletPositionsSource,
)
from market_analyser.events import EventBus
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerRepository
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)
from market_analyser.portfolio.sources import MANUAL_POSITIONS_FILENAME
from market_analyser.ui_events.buffer import UIEventBuffer
from market_analyser.user_drawings import UserDrawingsMirror


def create_mcp_components(
    *,
    provider: MarketDataProvider,
    annotations_repository: AnnotationsRepository,
    event_bus: EventBus,
    ui_event_buffer: UIEventBuffer,
    user_drawings_mirror: UserDrawingsMirror | None = None,
    backfill_coordinator: BackfillCoordinator | None = None,
    backtest_runs_repository: BacktestRunsRepository | None = None,
    advice_ledger_repository: AdviceLedgerRepository | None = None,
    runs_dir: Path | None = None,
    wallet_positions_sources: Mapping[str, WalletPositionsSource] | None = None,
    lp_detail_sources: Mapping[str, LpPositionDetailSource] | None = None,
    tx_history_sources: Mapping[str, TxHistorySource] | None = None,
    defi_tx_repository: DefiTxRepository | None = None,
    historical_price_source: HistoricalPriceSource | None = None,
    metric_points_repository: MetricPointsRepository | None = None,
    derivatives_source: DerivativesSource | None = None,
    mvrv_source: MetricSeriesSource | None = None,
    watches_repository: WatchesRepository | None = None,
    alerts_repository: AlertsRepository | None = None,
    account_holdings_sources: Mapping[str, AccountHoldingsSource] | None = None,
    manual_positions_path: Path | None = None,
    prediction_market_sources: Mapping[str, PredictionMarketSource] | None = None,
    executable_quote_sources: Mapping[str, ExecutableQuoteSource] | None = None,
    gauge_resolution_sources: Mapping[str, GaugeResolutionSource] | None = None,
    unclaimed_rewards_sources: Mapping[str, UnclaimedRewardsSource] | None = None,
    defi_dust_tokens: frozenset[str] = frozenset(),
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

    register_get_ohlcv(server, provider=provider, backfill_coordinator=backfill_coordinator)
    register_backfill_ohlcv(server, backfill_coordinator=backfill_coordinator)
    register_write_annotation(server, annotations_repository=annotations_repository)
    register_list_annotations(server, annotations_repository=annotations_repository)

    register_show_chart(server, event_bus=event_bus)
    register_update_chart(server, event_bus=event_bus)
    # Freeform agent annotations (Plan 0097, ADR-0091): declarative replace of
    # the agent drawing set per symbol via `chart.annotations v1`. Display-only,
    # never persisted sidecar-side — no repo dep.
    register_annotate_chart(server, event_bus=event_bus)
    # Drawing read-back (Plan 0104, ADR-0099): the agent reads the user's drawings
    # from the in-memory mirror the `PUT /user_drawings` route feeds. Always
    # registered — the mirror needs no external deps; when the caller supplies none
    # (the apiref wiring), a fresh empty mirror is used for introspection.
    register_get_chart_drawings(
        server,
        user_drawings_mirror=(
            user_drawings_mirror if user_drawings_mirror is not None else UserDrawingsMirror()
        ),
    )
    register_highlight_pattern(
        server, annotations_repository=annotations_repository, event_bus=event_bus
    )
    # Pattern sweep (Plan 0049): detect every pattern in a range and publish them
    # all in one `chart.highlight` event. Derived, not persisted — no repo dep.
    register_scan_patterns(server, provider=provider, event_bus=event_bus)
    # Support/resistance detection (Plan 0051): compute clustered volume-weighted
    # levels and auto-draw them as `price_line` overlays in one `chart.show`.
    # Derived, not persisted — no repo dep.
    register_detect_levels(server, provider=provider, event_bus=event_bus)
    # Classical chart patterns (Plan 0052): detect H&S/doubles/triangles/wedges
    # and draw their necklines/trendlines via one layer-only `chart.trendlines`
    # event carrying TrendlineSpecs (dashed=forming, solid=confirmed; ADR-0059).
    # Derived, not persisted.
    register_detect_chart_patterns(server, provider=provider, event_bus=event_bus)

    # Live-signal evaluator (Plan 0026): resolves a strategy, fetches fresh bars
    # to now (fetch-on-miss via the coordinator when present, else the plain
    # provider fetch), evaluates the current bar, and publishes
    # `signal.evaluated v1`. Always registered — no extra deps beyond the
    # always-present provider + event bus; the coordinator may be None.
    register_evaluate_signals(
        server,
        provider=provider,
        backfill_coordinator=backfill_coordinator,
        event_bus=event_bus,
    )

    register_get_pending_ui_events(server, ui_event_buffer=ui_event_buffer)

    # Always registered (no extra deps) — these dispatch through the provider
    # Protocol; the adapters stay package-internal (ADR-0007).
    register_screener_query(server, provider=provider)
    register_analyze_symbol(server, provider=provider)
    register_compare_strategies(server, provider=provider)
    register_walk_forward_backtest(server, provider=provider)
    register_multi_timeframe_analysis(server, provider=provider)
    # Single-symbol volume reads (Plan 0021/0090; unified Plan 0109 ph5, ADR-0104): one
    # `volume_read(kind=…)` verb folding volume_confirmation and counter_trend_volume
    # (anchored to the ADR-0083 snapshot trend) into modes over a shared cached-bar read.
    # Returns the {kind, result, partial_reason, scanned_at} envelope; conditions only.
    register_volume_read(server, provider=provider)
    register_detect_divergences(server, provider=provider, event_bus=event_bus)
    # Price-structure reads (Plan 0092; unified Plan 0109 ph4, ADR-0104): one
    # `price_structure(kind=…)` verb folding the Fibonacci grid, classic pivots,
    # anchored VWAP, and market structure (ADR-0084 second trend read) into modes over
    # a shared cached-bar read. Returns the {kind, result, partial_reason, scanned_at}
    # envelope; conditions only, no chart events.
    register_price_structure(server, provider=provider)
    # Unified watchlist scanner (Plan 0109, ADR-0104): one `scan_watchlist(rank_by=…)`
    # verb folding the six same-verb scanners — squeeze / gainers / losers / momentum /
    # quality / volume_breakout / smart_volume — into modes over the shared
    # `_scan_symbols` fan-out (ADR-0095) and the `analysis/volume` conditions, unchanged.
    # Always registered — provider-only, conditions only (the `quality` mode stays an
    # ADR-0096 screening rank; a call goes through `recommend`, which consumes the
    # underlying quality scorer directly, not this tool).
    register_scan_watchlist(server, provider=provider)
    register_search_symbols(server, provider=provider)
    register_quote_for(server, provider=provider)
    register_news_for(server, provider=provider)
    # Unified sentiment tool (Plan 0109, ADR-0104): one `sentiment(source=…)` verb over
    # a source registry — `news` (RSS + VADER) and `stocktwits` (crowd labels) today; a
    # new source (0103 Reddit / 0108 social) binds as one enum value + one registry entry.
    register_sentiment(server, provider=provider)
    register_crypto_fear_greed(server, provider=provider)
    register_bitcoin_market_pulse(server, provider=provider)
    register_market_snapshot(server, provider=provider)

    # Prediction-market odds (Plan 0040, ADR-0041): read-only Polymarket odds via
    # the ADR-0031 selector registry. Keyless (public Gamma reads — no secret, no
    # signing, no funds), so it is always registered: an explicit registry wins
    # (tests inject a spy); otherwise the keyless Polymarket adapter is the default.
    # Construction is network-free (it only builds the resilient client), so the
    # default reaches the network only on an actual search/odds call.
    resolved_prediction_market_sources = (
        prediction_market_sources
        if prediction_market_sources is not None
        else {"polymarket": PolymarketOddsAdapter()}
    )
    register_prediction_market_tools(
        server,
        prediction_market_sources=resolved_prediction_market_sources,
    )

    # Convergence screener (Plan 0078, ADR-0041/0029): the read-only analysis layer
    # over the same prediction-market registry — finds near-decided markets and
    # surfaces the edge WITH its risk context (resolution risk, liquidity caution,
    # capital-lockup note). Always registered alongside the keyless odds tools; it
    # reports opportunities as facts, never a buy call, and publishes
    # `prediction.screen_completed v1` so the viewer renders them live.
    register_prediction_screener(
        server,
        prediction_market_sources=resolved_prediction_market_sources,
        event_bus=event_bus,
    )

    # Cross-pool discrepancy scanner (Plan 0079/0086, ADR-0072 BA-7 / ADR-0080): the
    # read-only arb-viability evidence tool over the ADR-0031 executable-quote
    # selector registry (constant-product + concentrated-liquidity venues). Always
    # registered — it queries every wired source at call time, so it registers even
    # with an empty registry (returning `unconfigured` until secrets-store-backed
    # sources are wired by create_app). Reports net-of-cost discrepancies as facts;
    # signs nothing, moves no funds.
    register_pool_discrepancies(server, executable_quote_sources=executable_quote_sources or {})

    # `forecast` (Plan 0036, multi-horizon per Plan 0059): direction-as-probability
    # over cached bars, per-horizon gated on beating a naive baseline
    # out-of-sample. Always registered (needs only the provider). Accepted models
    # persist under a gitignored models/ root sibling to runs/ (ADR-0040) when a
    # runs_dir is wired; without one, the forecast still computes and returns, it
    # is simply not cached to disk. The metric store (when wired) enables the v2
    # exogenous feature set (ADR-0054); without it the tool computes on the v1
    # OHLCV-only set and its provenance says so (feature_set_id + empty
    # series_inputs) — explicit, not silent. Publishes `forecast.completed v1`
    # (Plan 0037) so the viewer's Forecast view renders the blocks live.
    # Plan 0063 (ADR-0058): with a runs_dir wired the tool also persists the
    # per-call explanation JSON under runs_dir/forecast/…; without one the
    # explanation summary still rides the wire, only the artifact is skipped.
    # Plan 0109 (ADR-0104): `forecast` is now the unified verb — kind ∈ {direction
    # (default), volatility, regime}, returning the ForecastResponse{kind, result}
    # envelope; each kind keeps its own result model and its `*.completed v1` event.
    # The two non-directional kinds (Plan 0077, ADR-0070) are read-only condition
    # reports with no model persistence; metric_lookup enables the v2 exogenous set
    # for every kind.
    forecast_models_dir = runs_dir.parent / "models" if runs_dir is not None else None
    register_forecast(
        server,
        provider=provider,
        event_bus=event_bus,
        models_dir=forecast_models_dir,
        metric_lookup=metric_points_repository,
        runs_dir=runs_dir,
    )

    # `recommend` (Plan 0038, ADR-0029): the advisor layer's labeled advisory
    # output — fuses the condition snapshot, the live strategy signal, the
    # walk-forward edge, and the forecast into one Recommendation, or an honest
    # "no actionable edge". Advisory only: holds no trade key, places no order.
    # Shares the forecast models_dir so an accepted forecast model persists once.
    # Plan 0066 (ADR-0057): shares the SAME metric store as the `forecast` tool
    # so the advisor's forecast leg walks the identical v2-full -> v2-deep -> v1
    # tier ladder — `recommend` and `forecast` no longer disagree at a horizon.
    # Publishes `recommendation.completed v1` (Plan 0039) so the viewer's
    # Recommendations view renders the advisory call live.
    # Plan 0063 (ADR-0058): with a runs_dir wired the tool also persists the
    # per-call advice explanation (verdict + trace + leg inputs) under
    # runs_dir/advice/…; the trace itself always rides the wire.
    register_recommend(
        server,
        provider=provider,
        backfill_coordinator=backfill_coordinator,
        event_bus=event_bus,
        models_dir=forecast_models_dir,
        metric_lookup=metric_points_repository,
        runs_dir=runs_dir,
        # Plan 0080 (ADR-0075): the append-only track-record index. Wired
        # whenever persistence exists; without it the tool still returns and
        # publishes, the call is simply not recorded for scoring.
        advice_ledger_repository=advice_ledger_repository,
    )

    # `technical_read` (Plan 0074, ADR-0068): the LESSER advisory tier — a single-
    # indicator mechanical direction (long/short/flat) with no conviction and no
    # levels, a distinct type + event from the fused `recommend` so the thin read
    # can never be mistaken for the corroborated call. Read-only: no key, no order,
    # no network write. Publishes `technical_read.completed v1` so the viewer renders
    # the read live beside (not merged into) the Recommendations surface.
    register_technical_read(
        server,
        provider=provider,
        event_bus=event_bus,
    )

    # `get_track_record` (Plan 0080, ADR-0075): the read-only surface over the
    # advisor's own live track record — hit-rate + calibration + baseline delta
    # over the scored recommendation rows, honest small-n. Registered whenever the
    # ledger exists (persistence wired); a factual record, never advice.
    if advice_ledger_repository is not None:
        register_get_track_record(
            server,
            advice_ledger_repository=advice_ledger_repository,
        )

    if backtest_runs_repository is not None and runs_dir is not None:
        register_run_backtest(
            server,
            provider=provider,
            repository=backtest_runs_repository,
            event_bus=event_bus,
            runs_dir=runs_dir,
        )
        # `get_backtest` (Plan 0050 phase 3): the MCP path to a persisted run's
        # full trades + metrics (equity opt-in/paged). Same disk+index deps as
        # run_backtest, so it registers in the same conditional block.
        register_get_backtest(
            server,
            repository=backtest_runs_repository,
            runs_dir=runs_dir,
        )

    # The metric-series toolset (Plan 0055, ADR-0051) is registered when the
    # metric-points repository is wired (create_app builds it from the SQLite
    # engine). `btc_cycle_snapshot` reads cached bars through the provider and
    # F&G/dominance through the store; `get_metric_series` pages any registered
    # series per ADR-0046. Legacy callers without persistence keep the smaller
    # toolset; nothing silently degrades.
    if metric_points_repository is not None:
        # MVRV refresh path (Plan 0057 phase 5): like `derivatives_snapshot`'s
        # source, the snapshot reads MVRV from the store offline and only touches
        # the network on `refresh=true`. Default-construct the keyless
        # CoinMetrics adapter when the caller injects none (tests inject a spy);
        # construction is network-free, so an unwired build never reaches out.
        register_btc_cycle_snapshot(
            server,
            provider=provider,
            metric_points_repository=metric_points_repository,
            mvrv_source=(
                mvrv_source
                if mvrv_source is not None
                else CoinMetricsCommunityAdapter(metric_store=metric_points_repository)
            ),
        )
        register_get_metric_series(
            server,
            metric_points_repository=metric_points_repository,
        )
        # `derivatives_snapshot` (Plan 0056) reads funding/OI from the same
        # store and is offline by default — only an explicit `refresh=true`
        # touches the network, so the real Binance adapter is a safe default
        # to construct here when the caller (tests inject spies) supplies
        # none. The adapter stays package-internal: this assembly hub is the
        # MCP side of the composition root (ADR-0007/ADR-0031).
        register_derivatives_snapshot(
            server,
            metric_points_repository=metric_points_repository,
            derivatives_source=(
                derivatives_source
                if derivatives_source is not None
                else BinanceDerivativesAdapter(metric_store=metric_points_repository)
            ),
        )

    # The watch toolset (Plan 0060, ADR-0055) is registered when both alerting
    # repositories are wired (create_app builds them from the SQLite engine).
    # The scheduler that evaluates the watches lives in the app lifespan, not
    # here — these tools only manage definitions and read history. Legacy
    # callers without persistence keep the smaller toolset; nothing silently
    # degrades.
    if watches_repository is not None and alerts_repository is not None:
        register_watch_tools(
            server,
            watches_repository=watches_repository,
            alerts_repository=alerts_repository,
        )

    # `scan_wallet` (Plan 0032) is registered only when a wallet-positions source
    # is wired (the adapter needs the secrets store); without it the DeFi toolset
    # is simply absent, nothing degrades.
    if wallet_positions_sources:
        register_scan_wallet(
            server,
            wallet_positions_sources=wallet_positions_sources,
            event_bus=event_bus,
            lp_detail_sources=lp_detail_sources,
        )

    # `compute_wallet_pnl` (Plan 0035) needs the whole P&L pipeline: the tx
    # source + discovery (both key off the secrets store), the immutable
    # decoded-tx cache (persistence), and a historical price source. Absent any
    # piece, the tool is simply not registered — nothing silently degrades.
    if (
        tx_history_sources
        and wallet_positions_sources
        and defi_tx_repository is not None
        and historical_price_source is not None
    ):
        register_compute_wallet_pnl(
            server,
            tx_history_sources=tx_history_sources,
            wallet_positions_sources=wallet_positions_sources,
            historical_price_source=historical_price_source,
            defi_tx_repository=defi_tx_repository,
            event_bus=event_bus,
            gauge_source=(gauge_resolution_sources or {}).get("rpc"),
            unclaimed_rewards_source=(unclaimed_rewards_sources or {}).get("rpc"),
            dust_tokens=defi_dust_tokens,
        )

    # `portfolio_summary` (Plan 0041, ADR-0042): the cross-venue read-only
    # holdings view. Registered when an account-holdings source is wired (the
    # Binance read adapter keys off the secrets store, like the DeFi sources);
    # the DeFi legs reuse whatever the DeFi toolset already wired and degrade
    # to typed leg_errors/notes when a piece is absent — nothing crashes, and
    # nothing silently pretends a leg was read. The manual positions file
    # defaults to the gitignored positions/ home (ADR-0042).
    if account_holdings_sources:
        register_portfolio_summary(
            server,
            provider=provider,
            account_holdings_sources=account_holdings_sources,
            manual_positions_path=(
                manual_positions_path
                if manual_positions_path is not None
                else Path("positions") / MANUAL_POSITIONS_FILENAME
            ),
            wallet_positions_sources=wallet_positions_sources,
            tx_history_sources=tx_history_sources,
            defi_tx_repository=defi_tx_repository,
            historical_price_source=historical_price_source,
        )

    # streamable_http_app() also lazily constructs the session manager; we call
    # it for that side effect even though we discard the returned Starlette app.
    server.streamable_http_app()
    session_manager = server.session_manager
    asgi_app = StreamableHTTPASGIApp(session_manager)
    return session_manager, asgi_app
