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
from market_analyser.api.mcp_tools.backfill_ohlcv import register_backfill_ohlcv
from market_analyser.api.mcp_tools.bitcoin_market_pulse import register_bitcoin_market_pulse
from market_analyser.api.mcp_tools.compare_strategies import register_compare_strategies
from market_analyser.api.mcp_tools.crypto_fear_greed import register_crypto_fear_greed
from market_analyser.api.mcp_tools.cycle_snapshot import register_btc_cycle_snapshot
from market_analyser.api.mcp_tools.detect_levels import register_detect_levels
from market_analyser.api.mcp_tools.evaluate_signals import register_evaluate_signals
from market_analyser.api.mcp_tools.forecast import register_forecast
from market_analyser.api.mcp_tools.get_backtest import register_get_backtest
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
from market_analyser.api.mcp_tools.quote_for import register_quote_for
from market_analyser.api.mcp_tools.run_backtest import register_run_backtest
from market_analyser.api.mcp_tools.scan_patterns import register_scan_patterns
from market_analyser.api.mcp_tools.scan_wallet import register_scan_wallet
from market_analyser.api.mcp_tools.screener_query import register_screener_query
from market_analyser.api.mcp_tools.search_symbols import register_search_symbols
from market_analyser.api.mcp_tools.sentiment_for_news import register_sentiment_for_news
from market_analyser.api.mcp_tools.show_chart import register_show_chart
from market_analyser.api.mcp_tools.smart_volume import register_smart_volume
from market_analyser.api.mcp_tools.stocktwits_sentiment import register_stocktwits_sentiment
from market_analyser.api.mcp_tools.update_chart import register_update_chart
from market_analyser.api.mcp_tools.volume_breakout import register_volume_breakout
from market_analyser.api.mcp_tools.volume_confirmation import register_volume_confirmation
from market_analyser.api.mcp_tools.walk_forward_backtest import register_walk_forward_backtest
from market_analyser.api.mcp_tools.write_annotation import register_write_annotation
from market_analyser.api.ui_events.buffer import UIEventBuffer
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import LpPositionDetailSource, WalletPositionsSource
from market_analyser.events import EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository


def create_mcp_components(
    *,
    provider: MarketDataProvider,
    annotations_repository: AnnotationsRepository,
    event_bus: EventBus,
    ui_event_buffer: UIEventBuffer,
    backfill_coordinator: BackfillCoordinator | None = None,
    backtest_runs_repository: BacktestRunsRepository | None = None,
    runs_dir: Path | None = None,
    wallet_positions_sources: Mapping[str, WalletPositionsSource] | None = None,
    lp_detail_sources: Mapping[str, LpPositionDetailSource] | None = None,
    metric_points_repository: MetricPointsRepository | None = None,
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
    register_volume_breakout(server, provider=provider)
    register_volume_confirmation(server, provider=provider)
    register_smart_volume(server, provider=provider)
    register_search_symbols(server, provider=provider)
    register_quote_for(server, provider=provider)
    register_news_for(server, provider=provider)
    register_sentiment_for_news(server, provider=provider)
    register_crypto_fear_greed(server, provider=provider)
    register_bitcoin_market_pulse(server, provider=provider)
    register_market_snapshot(server, provider=provider)
    register_stocktwits_sentiment(server, provider=provider)

    # `forecast` (Plan 0036): direction-as-probability over cached bars, gated on
    # beating a naive baseline out-of-sample. Always registered (needs only the
    # provider). Accepted models persist under a gitignored models/ root sibling to
    # runs/ (ADR-0040) when a runs_dir is wired; without one, the forecast still
    # computes and returns, it is simply not cached to disk.
    forecast_models_dir = runs_dir.parent / "models" if runs_dir is not None else None
    register_forecast(server, provider=provider, models_dir=forecast_models_dir)

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
        register_btc_cycle_snapshot(
            server,
            provider=provider,
            metric_points_repository=metric_points_repository,
        )
        register_get_metric_series(
            server,
            metric_points_repository=metric_points_repository,
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

    # streamable_http_app() also lazily constructs the session manager; we call
    # it for that side effect even though we discard the returned Starlette app.
    server.streamable_http_app()
    session_manager = server.session_manager
    asgi_app = StreamableHTTPASGIApp(session_manager)
    return session_manager, asgi_app
