"""Plan 0009 phase 2 — the `screener_query` MCP tool.

Drives the tool in-process via `FastMCP.call_tool` (no live server needed): the
boundary validation (extra-key rejection, limit/market bounds, filters-None) all
fire at FastMCP's argument-model layer before the tool body runs. A fake provider
supplies rows and, for the responsiveness test, a deliberate delay so the
`asyncio.to_thread` offload can be observed.
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

from market_analyser.api.mcp_tools.screener_query import register_screener_query
from market_analyser.data.types import (
    Bar,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)


class _FakeProvider:
    """Full MarketDataProvider stub; only get_screener is exercised."""

    def __init__(self, rows: Sequence[ScreenerRow], *, delay: float = 0.0) -> None:
        self._rows = list(rows)
        self._delay = delay
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {"filters": filters, "market": market, "exchange": exchange, "limit": limit},
        )
        if self._delay:
            time.sleep(self._delay)
        return list(self._rows)

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        as_of: datetime | None = None,
    ) -> SentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str,
        window: str,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


def _server(provider: _FakeProvider) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_screener_query(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    # call_tool returns (content_blocks, structured_dict) when the tool declares a
    # structured (dict) return; typed as Any here so the 2-tuple unpacks cleanly.
    return anyio.run(server.call_tool, "screener_query", arguments)


def test_happy_path_returns_rows_and_passes_params() -> None:
    rows = [
        ScreenerRow(symbol="AAA", fields={"RSI": 25.0}),
        ScreenerRow(symbol="BBB", fields={"RSI": 28.0}),
    ]
    provider = _FakeProvider(rows)
    server = _server(provider)

    _content, structured = _call(
        server,
        {"params": {"filters": {"RSI": {"lt": 30}}, "market": "america", "limit": 50}},
    )

    assert len(structured["rows"]) == 2
    assert {row["symbol"] for row in structured["rows"]} == {"AAA", "BBB"}
    assert "queried_at" in structured
    assert provider.calls[0]["market"] == "america"
    assert provider.calls[0]["limit"] == 50


def test_rejects_unknown_key() -> None:
    server = _server(_FakeProvider([]))
    with pytest.raises(ToolError):
        _call(server, {"params": {"filters": {}, "bogus_key": 1}})


def test_rejects_over_limit_at_boundary() -> None:
    server = _server(_FakeProvider([]))
    with pytest.raises(ToolError):
        _call(server, {"params": {"filters": {}, "limit": 201}})


def test_rejects_unknown_market_at_boundary() -> None:
    server = _server(_FakeProvider([]))
    with pytest.raises(ToolError):
        _call(server, {"params": {"filters": {}, "market": "narnia"}})


def test_empty_filters_accepted() -> None:
    server = _server(_FakeProvider([ScreenerRow(symbol="AAA", fields={})]))
    _content, structured = _call(server, {"params": {"filters": {}}})
    assert structured["rows"] == [{"symbol": "AAA", "fields": {}}]


def test_filters_none_rejected() -> None:
    server = _server(_FakeProvider([]))
    with pytest.raises(ToolError):
        _call(server, {"params": {"filters": None}})


def test_event_loop_not_blocked_by_slow_provider() -> None:
    provider = _FakeProvider([ScreenerRow(symbol="AAA", fields={})], delay=0.1)
    server = _server(provider)

    async def _run() -> float:
        start = time.perf_counter()
        await asyncio.gather(
            *(server.call_tool("screener_query", {"params": {"filters": {}}}) for _ in range(5)),
        )
        return time.perf_counter() - start

    elapsed = anyio.run(_run)
    # Five 100ms calls serialized on the event loop would take >= 0.5s. Offloaded
    # to threads via asyncio.to_thread they overlap, so the wall time stays low.
    assert elapsed < 0.35
