"""Plan 0032 phase 4: the `scan_wallet` MCP tool.

Drives the tool in-process via `FastMCP.call_tool`. A fake `WalletPositionsSource`
supplies positions — or raises a typed error. Asserts the decoded positions come
back with the scan_* events streamed, an invalid address is rejected at the input
boundary, a missing key surfaces as a structured `auth` error, and the tool is
registered both directly and in the full `create_mcp_components` toolset.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ListToolsRequest, ListToolsResult

from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.mcp_tools.scan_wallet import register_scan_wallet
from market_analyser.api.ui_events.buffer import UIEventBuffer
from market_analyser.data.adapters.zerion import ZerionAuthError
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.defi.models import DefiPosition, PositionToken
from market_analyser.events import Envelope, EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_WALLET = "0x1111111111111111111111111111111111111111"


class _FakeSource:
    def __init__(
        self,
        positions: Sequence[DefiPosition] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._positions = list(positions or [])
        self._error = error

    def fetch_positions(self, address: str) -> Sequence[DefiPosition]:
        if self._error is not None:
            raise self._error
        return self._positions


def _position(chain: str = "ethereum", symbol: str = "USDC") -> DefiPosition:
    return DefiPosition(
        position_id=f"{chain}:aave-v3:{symbol}",
        chain=chain,  # type: ignore[arg-type]  # known-good chain
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[PositionToken(symbol=symbol, address="0xabc", amount=1.0)],
        usd_value=1000.0,
    )


def _server(source: _FakeSource, bus: EventBus) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_scan_wallet(server, wallet_positions_sources={"zerion": source}, event_bus=bus)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "scan_wallet", arguments)


def _drain(bus_sub_queue: asyncio.Queue[Envelope]) -> list[str]:
    types: list[str] = []
    while not bus_sub_queue.empty():
        types.append(bus_sub_queue.get_nowait().type)
    return types


def test_happy_path_returns_positions_and_streams_events() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    server = _server(_FakeSource([_position("ethereum"), _position("base", "cbBTC")]), bus)

    _content, structured = _call(server, {"params": {"address": _WALLET}})

    assert structured["error"] is None
    assert structured["position_count"] == 2
    assert len(structured["positions"]) == 2
    assert structured["wallet"] == "0x1111…1111"  # masked
    assert _WALLET not in str(structured)  # full address never surfaced
    types = _drain(sub.queue)
    assert types[0] == "defi.scan_started"
    assert types[-1] == "defi.scan_completed"
    assert "defi.scan_progress" in types


def test_invalid_address_rejected_at_input_boundary() -> None:
    server = _server(_FakeSource([_position()]), EventBus())
    with pytest.raises(ToolError):
        _call(server, {"params": {"address": "not-an-address"}})


def test_extra_key_rejected_at_input_boundary() -> None:
    server = _server(_FakeSource([_position()]), EventBus())
    with pytest.raises(ToolError):
        _call(server, {"params": {"address": _WALLET, "chain": "ethereum"}})


def test_missing_key_returns_structured_auth_error() -> None:
    server = _server(
        _FakeSource(error=ZerionAuthError("zerion: no API key configured")), EventBus()
    )
    _content, structured = _call(server, {"params": {"address": _WALLET}})
    assert structured["positions"] is None
    assert structured["error"] == "auth"
    assert "key" in structured["message"].lower()


def test_upstream_error_returns_structured_reason() -> None:
    server = _server(_FakeSource(error=UpstreamUnavailableError("zerion: 503")), EventBus())
    _content, structured = _call(server, {"params": {"address": _WALLET}})
    assert structured["positions"] is None
    assert structured["error"] == "upstream_unavailable"


def test_tool_is_registered_directly() -> None:
    server = _server(_FakeSource([_position()]), EventBus())
    tools = anyio.run(server.list_tools)
    assert "scan_wallet" in {tool.name for tool in tools}


def test_description_advertises_chains_and_auth_recovery() -> None:
    server = _server(_FakeSource([_position()]), EventBus())
    tool = next(t for t in anyio.run(server.list_tools) if t.name == "scan_wallet")
    description = (tool.description or "").lower()
    assert "address" in description
    assert "auth" in description  # advertises the set-your-key recovery path


# --- full-toolset registration (the wired server) --------------------------------


class _FakeProvider:
    """Minimal MarketDataProvider stub for building the full MCP server."""

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
        filters: dict[str, str | float | None],
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

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
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


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


def _full_server_tool_names(
    annotations_repo: AnnotationsRepository,
    *,
    with_sources: bool,
) -> set[str]:
    sources = {"zerion": _FakeSource([_position()])} if with_sources else None
    session_manager, _asgi = create_mcp_components(
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        event_bus=EventBus(),
        ui_event_buffer=UIEventBuffer(),
        wallet_positions_sources=sources,
    )
    # Enumerate the wired server's tools via its low-level ListTools handler — the
    # FastMCP instance itself is internal to create_mcp_components.
    handler = session_manager.app.request_handlers[ListToolsRequest]
    result = anyio.run(handler, ListToolsRequest(method="tools/list"))
    tools_result = result.root
    assert isinstance(tools_result, ListToolsResult)
    return {tool.name for tool in tools_result.tools}


def test_full_toolset_includes_scan_wallet(annotations_repo: AnnotationsRepository) -> None:
    names = _full_server_tool_names(annotations_repo, with_sources=True)
    assert "scan_wallet" in names


def test_full_toolset_omits_scan_wallet_without_a_source(
    annotations_repo: AnnotationsRepository,
) -> None:
    names = _full_server_tool_names(annotations_repo, with_sources=False)
    assert "scan_wallet" not in names
