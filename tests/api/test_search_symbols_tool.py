"""Plan 0024 phase 2 — the `search_symbols` MCP tool.

Drives the tool in-process via `FastMCP.call_tool` (no live server needed). The
boundary validation (extra-key rejection) fires at FastMCP's argument-model layer
before the tool body runs. A fake provider supplies results and, for the
responsiveness test, a deliberate delay so the `asyncio.to_thread` offload can be
observed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.search_symbols import register_search_symbols
from market_analyser.data.types import (
    Bar,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)


class _FakeProvider:
    """Full MarketDataProvider stub; only search_symbols is exercised."""

    def __init__(self, results: Sequence[SymbolInfo], *, delay: float = 0.0) -> None:
        self._results = list(results)
        self._delay = delay
        self.calls: list[str] = []

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        self.calls.append(query)
        if self._delay:
            time.sleep(self._delay)
        return list(self._results)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise NotImplementedError

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
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
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
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


_BTC = SymbolInfo(
    symbol="BTC-USD", name="Bitcoin USD", exchange="CCC", quote_type="Cryptocurrency"
)


def _server(provider: _FakeProvider) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_search_symbols(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "search_symbols", arguments)


def test_happy_path_returns_results_and_queried_at() -> None:
    provider = _FakeProvider([_BTC])
    server = _server(provider)

    _content, structured = _call(server, {"params": {"query": "BTC"}})

    assert structured["results"] == [_BTC.model_dump()]
    assert structured["results"][0]["quote_type"] == "Cryptocurrency"
    assert "queried_at" in structured
    assert provider.calls == ["BTC"]


def test_zero_match_returns_empty_results() -> None:
    server = _server(_FakeProvider([]))
    _content, structured = _call(server, {"params": {"query": "zzz"}})
    assert structured["results"] == []


def test_rejects_unknown_key() -> None:
    server = _server(_FakeProvider([_BTC]))
    with pytest.raises(ToolError):
        _call(server, {"params": {"query": "BTC", "bogus_key": 1}})


def test_description_advertises_unknown_symbol_recovery_path() -> None:
    """Done-when: the description tells the agent to call this to resolve a loose
    name to a fetchable symbol, as the recovery path for get_ohlcv's
    unknown_symbol failure (ADR-0015 — the agent reads the description)."""
    server = _server(_FakeProvider([_BTC]))
    tools = anyio.run(server.list_tools)
    tool = next(t for t in tools if t.name == "search_symbols")
    description = (tool.description or "").lower()
    assert "unknown_symbol" in description
    assert "get_ohlcv" in description
    assert "fetchable" in description


def test_event_loop_not_blocked_by_slow_provider() -> None:
    provider = _FakeProvider([_BTC], delay=0.1)
    server = _server(provider)

    async def _run() -> float:
        start = time.perf_counter()
        await asyncio.gather(
            *(server.call_tool("search_symbols", {"params": {"query": "BTC"}}) for _ in range(5)),
        )
        return time.perf_counter() - start

    elapsed = anyio.run(_run)
    # Five 100ms calls serialised on the loop would take >= 0.5s; offloaded via
    # asyncio.to_thread they overlap, so wall time stays low.
    assert elapsed < 0.35
