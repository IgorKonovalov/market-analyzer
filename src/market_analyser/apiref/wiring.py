"""Fully-wired sidecar construction, shared by the apiref generator and the
full-toolset registration test (Plan 0070, ADR-0064).

Building the reference means seeing EVERY conditionally-registered surface at
once. MCP tools register only when their dependencies are present
(`create_mcp_components`): the DeFi tools need a wallet source, the metric-series
tools need the store, the watch tools need the alerting repositories, and so on.
The REST route groups mount conditionally in `create_app` the same way. This
module assembles one set of null / in-memory dependencies that lights up the
whole surface, so the generator and the test consume ONE wiring source of truth
rather than each inventing its own.

Two builders are exposed:

- `build_wired_mcp_server` returns the fully-wired `FastMCP` instance, for
  tool-registry introspection. `create_mcp_components` returns transport handles
  (`session_manager`, `asgi_app`) and never the server object, and the low-level
  MCP server it does expose carries no back-reference to the `FastMCP` — so we
  capture the instance during construction (see below) rather than fork the
  shipped wiring. The captured server's tool manager carries each tool's `fn`,
  which the wire `ListToolsResult` does not; the generator needs it for the
  source-file link and the return-annotation fallback.
- `build_wired_app` returns the fully-wired `FastAPI` app, for OpenAPI route
  introspection.

Everything is in-memory (`:memory:` SQLite) and network-free to construct — the
default adapters `create_mcp_components` / `create_app` build only reach the
network on an actual fetch, which introspection never triggers. The secrets
below are fixed, non-sensitive constants: this app never binds a socket or serves
a request, it exists only to be read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

import market_analyser.api.mcp_app as mcp_app
from market_analyser.api.app import create_app
from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.data.types import (
    AccountHoldings,
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.defi.models import Chain, DefiPosition
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.events import EventBus
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerRepository
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.backtest_runs import BacktestRunsRepository
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)
from market_analyser.ui_events.buffer import UIEventBuffer

_RENDERER_SECRET = "apiref-renderer-secret"
_MCP_SECRET = "apiref-mcp-secret"


class _StubProvider:
    """A `MarketDataProvider` that answers nothing — introspection reads static
    schemas, never bar data, so every method may raise. `get_ohlcv` returns an
    empty sequence only because the coverage-capable paths are never exercised."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return []

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: str = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError

    def get_market_sentiment(
        self,
        market: str,
        window: str = "current",
        as_of: datetime | None = None,
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self,
        market: str = "crypto",
        as_of: datetime | None = None,
    ) -> MacroContext:
        raise NotImplementedError


class _NullWalletSource:
    def fetch_positions(self, address: str) -> list[DefiPosition]:
        return []


class _NullTxSource:
    def fetch_transactions(
        self, address: str, *, min_mined_at: datetime | None = None
    ) -> list[DecodedTx]:
        return []


class _NullPriceSource:
    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return None


class _NullAccountSource:
    def fetch_account_holdings(self) -> AccountHoldings:
        return AccountHoldings(
            venue="binance", spot=[], futures=[], as_of=datetime(2026, 1, 1, tzinfo=UTC)
        )


def _in_memory_session_factory() -> sessionmaker[Session]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    return make_session_factory(engine)


def build_wired_mcp_server(runs_dir: Path) -> FastMCP:
    """Construct the fully-wired `FastMCP` server and return the instance.

    `create_mcp_components` builds the `FastMCP` internally and returns only the
    transport handles, so we temporarily swap `mcp_app.FastMCP` for a subclass
    that records the instance it constructs. This reuses the real wiring
    (every `register_*` call, in the real order, with every dependency present)
    while still surfacing the server object introspection needs — no second copy
    of the registration logic to drift.
    """
    session_factory = _in_memory_session_factory()
    captured: list[FastMCP] = []

    class _CapturingFastMCP(FastMCP):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            captured.append(self)

    # Swap the FastMCP symbol create_mcp_components resolves against for our
    # recording subclass. getattr/setattr (not attribute access) because
    # `mcp_app` re-exports FastMCP rather than defining it, and strict mypy's
    # no-implicit-reexport rule forbids reading it as an attribute.
    original = getattr(mcp_app, "FastMCP")  # noqa: B009
    setattr(mcp_app, "FastMCP", _CapturingFastMCP)  # noqa: B010
    try:
        create_mcp_components(
            provider=_StubProvider(),
            annotations_repository=AnnotationsRepository(session_factory),
            event_bus=EventBus(),
            ui_event_buffer=UIEventBuffer(),
            backtest_runs_repository=BacktestRunsRepository(session_factory),
            advice_ledger_repository=AdviceLedgerRepository(session_factory),
            runs_dir=runs_dir,
            wallet_positions_sources={"zerion": _NullWalletSource()},
            tx_history_sources={"zerion": _NullTxSource()},
            defi_tx_repository=DefiTxRepository(session_factory),
            historical_price_source=_NullPriceSource(),
            metric_points_repository=MetricPointsRepository(session_factory),
            watches_repository=WatchesRepository(session_factory),
            alerts_repository=AlertsRepository(session_factory),
            position_watches_repository=DefiPositionWatchesRepository(session_factory),
            position_alerts_repository=DefiPositionAlertsRepository(session_factory),
            account_holdings_sources={"binance": _NullAccountSource()},
            manual_positions_path=runs_dir / "portfolio.json",
        )
    finally:
        setattr(mcp_app, "FastMCP", original)  # noqa: B010

    if not captured:
        raise RuntimeError("apiref wiring: create_mcp_components built no FastMCP server")
    return captured[0]


def build_wired_app(runs_dir: Path) -> FastAPI:
    """Construct the fully-wired `FastAPI` app for OpenAPI route introspection.

    Passing an in-memory `engine` makes `create_app` build the persistence-gated
    repositories (annotations, backtests, metric store, alerting), which mounts
    every conditionally-registered REST route group; the MCP secret + path mount
    the settings router, and the wallet source mounts the DeFi router. The stub
    provider keeps `create_app` from constructing the real network adapters.
    """
    engine = make_engine(":memory:")
    return create_app(
        secret=_RENDERER_SECRET,
        mcp_secret=_MCP_SECRET,
        mcp_secret_path=runs_dir / "mcp-secret.json",
        engine=engine,
        provider=_StubProvider(),
        runs_dir=runs_dir,
        wallet_positions_sources={"zerion": _NullWalletSource()},
    )


@dataclass(frozen=True)
class WiredSurfaces:
    """The two introspectable live objects, built from one wiring pass."""

    server: FastMCP
    app: FastAPI


def build_wired_surfaces(runs_dir: Path) -> WiredSurfaces:
    """Build both the fully-wired MCP server and the fully-wired app."""
    return WiredSurfaces(
        server=build_wired_mcp_server(runs_dir),
        app=build_wired_app(runs_dir),
    )
