"""Plan 0019 phase 2 — the `quote_for` MCP tool.

Drives the tool in-process via `FastMCP.call_tool` (no live server needed). The
boundary validation (empty symbol, extra-key rejection, `as_of` absence) fires at
FastMCP's argument-model layer before the tool body runs. A fake provider supplies
the quote — or raises a typed `UpstreamDataError` — and, for the responsiveness
test, a deliberate delay so the `asyncio.to_thread` offload can be observed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.quote_for import QuoteForInput, register_quote_for
from market_analyser.data.errors import UnknownSymbolError, UpstreamUnavailableError
from market_analyser.data.types import (
    Bar,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)

_QUOTE = Quote(
    symbol="AAPL",
    price=189.95,
    as_of=datetime(2024, 5, 24, 10, 0, tzinfo=UTC),
    source="yahoo",
    change_pct=-0.5497382198952879,
    previous_close=191.0,
    day_high=190.12,
    day_low=188.0,
    week52_high=199.62,
    week52_low=164.08,
    currency="USD",
    market_state="REGULAR",
    volume=48000000.0,
)


class _FakeProvider:
    """Full MarketDataProvider stub; only get_quote is exercised. Either returns a
    fixed Quote or raises a supplied exception."""

    def __init__(
        self,
        quote: Quote | None = None,
        *,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._quote = quote
        self._error = error
        self._delay = delay
        self.calls: list[str] = []

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        self.calls.append(symbol)
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        assert self._quote is not None
        return self._quote

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
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


def _server(provider: _FakeProvider) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_quote_for(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "quote_for", arguments)


def test_happy_path_returns_all_quote_fields_and_queried_at() -> None:
    provider = _FakeProvider(_QUOTE)
    server = _server(provider)

    _content, structured = _call(server, {"params": {"symbol": "AAPL"}})

    assert structured["quote"] == _QUOTE.model_dump(mode="json")
    # Every additive Plan 0019 field is present and serialised.
    for field in (
        "price",
        "change_pct",
        "previous_close",
        "day_high",
        "day_low",
        "week52_high",
        "week52_low",
        "currency",
        "market_state",
        "volume",
    ):
        assert field in structured["quote"]
    assert structured["error"] is None
    assert structured["message"] is None
    assert "queried_at" in structured
    assert provider.calls == ["AAPL"]


def test_rejects_empty_symbol() -> None:
    server = _server(_FakeProvider(_QUOTE))
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": ""}})


def test_rejects_unknown_key() -> None:
    server = _server(_FakeProvider(_QUOTE))
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "AAPL", "bogus_key": 1}})


def test_as_of_is_not_an_input_field() -> None:
    """The input model has no `as_of`, so passing one is an unknown key (a live
    quote is not replayable — Plan 0019)."""
    assert "as_of" not in QuoteForInput.model_fields
    server = _server(_FakeProvider(_QUOTE))
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "AAPL", "as_of": "2026-01-01T00:00:00Z"}})


def test_unknown_symbol_returns_structured_error_not_500() -> None:
    provider = _FakeProvider(error=UnknownSymbolError("yahoo: no quote for 'NOPE'", symbol="NOPE"))
    server = _server(provider)

    _content, structured = _call(server, {"params": {"symbol": "NOPE"}})

    assert structured["quote"] is None
    assert structured["error"] == "unknown_symbol"
    assert "NOPE" in structured["message"]
    assert "queried_at" in structured


def test_upstream_unavailable_returns_structured_error() -> None:
    """The structured-error courtesy extends to every typed UpstreamDataError, not
    just unknown-symbol (shared `failure_reason` map)."""
    err = UpstreamUnavailableError("yahoo: upstream unavailable (HTTP 503)")
    provider = _FakeProvider(error=err)
    server = _server(provider)

    _content, structured = _call(server, {"params": {"symbol": "AAPL"}})

    assert structured["quote"] is None
    assert structured["error"] == "upstream_unavailable"


def test_description_advertises_live_and_unknown_symbol() -> None:
    server = _server(_FakeProvider(_QUOTE))
    tools = anyio.run(server.list_tools)
    tool = next(t for t in tools if t.name == "quote_for")
    description = (tool.description or "").lower()
    assert "unknown_symbol" in description
    assert "as_of" in description  # advertises that there is no historical replay


def test_event_loop_not_blocked_by_slow_provider() -> None:
    provider = _FakeProvider(_QUOTE, delay=0.1)
    server = _server(provider)

    async def _run() -> float:
        start = time.perf_counter()
        await asyncio.gather(
            *(server.call_tool("quote_for", {"params": {"symbol": "AAPL"}}) for _ in range(5)),
        )
        return time.perf_counter() - start

    elapsed = anyio.run(_run)
    # Five 100ms calls serialised on the loop would take >= 0.5s; offloaded via
    # asyncio.to_thread they overlap, so wall time stays low.
    assert elapsed < 0.35
